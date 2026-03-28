"""Management API routes for the SettleBridge Gateway."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.models.gateway import (
    AlertEvent,
    AlertRule,
    AuditEntry,
    GatewayAgent,
    PolicyDecisionType,
    TrustPolicy,
)
from app.schemas.gateway import (
    AgentDetailResponse,
    AgentHealthResponse,
    AlertEventResponse,
    AlertListResponse,
    AlertRuleCreate,
    AlertRuleResponse,
    AlertRuleUpdate,
    AuditEntryResponse,
    AuditListResponse,
    ClaimAgentRequest,
    ExchangeAgentSearchResult,
    GatewayAgentResponse,
    RegisterUtilityAgentRequest,
    RegisterUtilityAgentResponse,
    GatewayHealthResponse,
    GatewayMetricsResponse,
    PolicyValidationResult,
    ReputationSnapshotResponse,
    SettlementOverviewResponse,
    TrustPolicyCreate,
    TrustPolicyResponse,
    TrustPolicyUpdate,
)

router = APIRouter()

_start_time = time.time()

# These get injected at startup from main.py
_gateway_components: dict[str, Any] = {}


def set_gateway_components(components: dict[str, Any]) -> None:
    _gateway_components.update(components)


# ---- Health ----


@router.get("/health", response_model=GatewayHealthResponse)
async def gateway_health(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.main import get_exchange_stats_cache

    health_mon = _gateway_components.get("health_monitor")
    startup = _gateway_components.get("startup")

    agents = health_mon.get_all_agents() if health_mon else []
    active_count = sum(1 for a in agents if a.status == "active")

    local_tx = (await db.execute(select(func.count(AuditEntry.id)))).scalar() or 0
    violations = (
        await db.execute(
            select(func.count(AuditEntry.id)).where(
                AuditEntry.policy_decision == PolicyDecisionType.BLOCK
            )
        )
    ).scalar() or 0

    ex_stats = get_exchange_stats_cache()
    exchange_tx = ex_stats.get("activity_24h", {}).get("transaction_count", 0)
    total_tx = local_tx + exchange_tx

    outcomes = ex_stats.get("settlement_outcomes") or {}
    violations += outcomes.get("refunded", 0)

    avg_lat = 0.0
    if agents:
        lats = [a.avg_latency_ms for a in agents if a.avg_latency_ms is not None]
        avg_lat = sum(lats) / len(lats) if lats else 0.0

    return GatewayHealthResponse(
        status="ok",
        uptime_seconds=time.time() - _start_time,
        active_agents=active_count,
        total_transactions=total_tx,
        policy_violations_24h=violations,
        avg_latency_ms=round(avg_lat, 2),
        exchange_connected=startup.exchange_connected if startup else False,
    )


# ---- Agents ----


@router.get("/agents", response_model=list[AgentHealthResponse])
async def list_agents(_user: User = Depends(get_current_user)):
    health_mon = _gateway_components.get("health_monitor")
    if not health_mon:
        return []
    rep_cache = _gateway_components.get("reputation_cache")
    agents = health_mon.get_all_agents()
    result = []
    for a in agents:
        score = await rep_cache.get(a.agent_id) if rep_cache else None
        result.append(AgentHealthResponse(
            agent_id=a.agent_id,
            bot_id=a.bot_id,
            status=a.status,
            reputation_score=score,
            avg_latency_ms=a.avg_latency_ms,
            error_rate=a.error_rate,
            request_count=a.request_count,
            last_seen=a.last_seen,
        ))
    return result


@router.get("/agents/{agent_id}/history", response_model=AgentDetailResponse)
async def agent_detail(
    agent_id: str,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    health_mon = _gateway_components.get("health_monitor")
    rep_cache = _gateway_components.get("reputation_cache")

    stats = health_mon.get_agent(agent_id) if health_mon else None
    score = await rep_cache.get(agent_id) if rep_cache else None

    history = []
    if rep_cache:
        snaps = await rep_cache.get_history(agent_id, db, limit=50)
        history = [ReputationSnapshotResponse.model_validate(s) for s in snaps]

    tx_result = await db.execute(
        select(AuditEntry)
        .where(
            (AuditEntry.source_agent == agent_id) | (AuditEntry.target_agent == agent_id)
        )
        .order_by(AuditEntry.timestamp.desc())
        .limit(20)
    )
    recent_tx = [AuditEntryResponse.model_validate(e) for e in tx_result.scalars().all()]

    return AgentDetailResponse(
        agent_id=agent_id,
        bot_id=stats.bot_id if stats else "",
        status=stats.status if stats else "unknown",
        reputation_score=score,
        avg_latency_ms=stats.avg_latency_ms if stats else None,
        error_rate=stats.error_rate if stats else None,
        request_count=stats.request_count if stats else 0,
        last_seen=stats.last_seen if stats else None,
        reputation_history=history,
        recent_transactions=recent_tx,
    )


# ---- Agent Claims ----


async def _claim_gateway_agent(
    db: AsyncSession,
    *,
    exchange_account_id: str,
    agent_api_key: str | None,
) -> GatewayAgent:
    """Create gateway_agents row, optional exchange claim, health monitor (shared by claim + register)."""
    from fastapi import HTTPException
    import httpx
    from a2a_settlement.client import SettlementExchangeClient

    existing = await db.execute(
        select(GatewayAgent).where(
            GatewayAgent.exchange_account_id == exchange_account_id,
            GatewayAgent.status == "active",
        )
    )
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail="Agent already claimed by this gateway")

    client = SettlementExchangeClient(base_url=settings.effective_exchange_url)
    try:
        acct = client.get_account(account_id=exchange_account_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Agent not found on exchange")

    bot_name = acct.get("bot_name", "")
    description = acct.get("description")
    skills = acct.get("skills", [])

    exchange_claim_id = None
    verified = False
    gw_api_key = settings.GATEWAY_EXCHANGE_API_KEY
    if gw_api_key:
        claim_url = f"{settings.effective_exchange_url.rstrip('/')}/v1/accounts/{exchange_account_id}/claim"
        claim_payload: dict[str, str] = {}
        if agent_api_key:
            claim_payload["agent_api_key"] = agent_api_key
        try:
            resp = httpx.post(
                claim_url,
                json=claim_payload or None,
                headers={"Authorization": f"Bearer {gw_api_key}"},
                timeout=10,
            )
            if resp.status_code == 201:
                data = resp.json()
                exchange_claim_id = data.get("claim_id")
                verified = data.get("verified", False)
        except Exception:
            pass

    agent = GatewayAgent(
        exchange_account_id=exchange_account_id,
        bot_name=bot_name,
        description=description,
        skills=skills,
        exchange_claim_id=exchange_claim_id,
        verified=verified,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    health_mon = _gateway_components.get("health_monitor")
    rep_cache = _gateway_components.get("reputation_cache")
    if health_mon:
        health_mon.register_agent(exchange_account_id, bot_id=bot_name)
        health_mon.mark_alive(exchange_account_id)
    if rep_cache:
        reputation = acct.get("reputation")
        if reputation is not None:
            await rep_cache.set(exchange_account_id, float(reputation))

    return agent


@router.post("/agents/register", response_model=RegisterUtilityAgentResponse, status_code=201)
async def register_utility_agent_via_gateway(
    request: Request,
    body: RegisterUtilityAgentRequest,
    db: AsyncSession = Depends(get_db),
):
    """Register on the settlement exchange and claim on this gateway in one step (provisioning secret)."""
    from fastapi import HTTPException
    from a2a_settlement.client import SettlementExchangeClient

    secret = request.headers.get("X-SettleBridge-Registration-Secret", "")
    if not settings.UTILITY_AGENT_REGISTRATION_SECRET or secret != settings.UTILITY_AGENT_REGISTRATION_SECRET:
        raise HTTPException(
            status_code=403,
            detail="Utility agent registration is disabled or the registration secret is invalid",
        )
    if not settings.GATEWAY_ENABLED:
        raise HTTPException(status_code=503, detail="Gateway is disabled")

    ex = SettlementExchangeClient(base_url=settings.effective_exchange_url)
    try:
        r = ex.register_account(
            bot_name=body.bot_name,
            developer_id=body.developer_id,
            developer_name=body.developer_name,
            contact_email=body.contact_email,
            description=body.description,
            skills=body.skills,
            daily_spend_limit=body.daily_spend_limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Exchange registration failed: {exc}") from exc

    acc = r.get("account") or {}
    account_id = acc.get("id")
    api_key = r.get("api_key")
    if not account_id or not api_key:
        raise HTTPException(status_code=502, detail="Exchange returned an incomplete registration response")

    agent = await _claim_gateway_agent(
        db,
        exchange_account_id=account_id,
        agent_api_key=api_key,
    )
    return RegisterUtilityAgentResponse(
        account_id=account_id,
        api_key=api_key,
        bot_name=agent.bot_name,
        gateway_row_id=agent.id,
        description=agent.description,
        skills=agent.skills,
    )


@router.get("/agents/claimed", response_model=list[GatewayAgentResponse])
async def list_claimed_agents(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(GatewayAgent).where(GatewayAgent.status == "active").order_by(GatewayAgent.claimed_at.desc())
    )
    return [GatewayAgentResponse.model_validate(a) for a in result.scalars().all()]


@router.post("/agents/claim", response_model=GatewayAgentResponse, status_code=201)
async def claim_agent(
    body: ClaimAgentRequest,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await _claim_gateway_agent(
        db,
        exchange_account_id=body.exchange_account_id,
        agent_api_key=body.agent_api_key,
    )
    return GatewayAgentResponse.model_validate(agent)


@router.delete("/agents/{exchange_account_id}/unclaim")
async def unclaim_agent(
    exchange_account_id: str,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from fastapi import HTTPException

    result = await db.execute(
        select(GatewayAgent).where(
            GatewayAgent.exchange_account_id == exchange_account_id,
            GatewayAgent.status == "active",
        )
    )
    agent = result.scalars().first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not claimed by this gateway")

    agent.status = "released"
    await db.commit()

    health_mon = _gateway_components.get("health_monitor")
    if health_mon:
        health_mon.unregister_agent(exchange_account_id)

    gw_api_key = settings.GATEWAY_EXCHANGE_API_KEY
    if gw_api_key:
        import httpx
        unclaim_url = f"{settings.effective_exchange_url.rstrip('/')}/v1/accounts/{exchange_account_id}/claim"
        try:
            httpx.delete(
                unclaim_url,
                headers={"Authorization": f"Bearer {gw_api_key}"},
                timeout=10,
            )
        except Exception:
            pass

    return {"status": "released", "exchange_account_id": exchange_account_id}


@router.get("/agents/exchange-directory", response_model=list[ExchangeAgentSearchResult])
async def search_exchange_directory(
    q: str | None = None,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search the exchange directory for agents to claim."""
    from app.services import exchange as exchange_svc

    try:
        directory = exchange_svc.get_directory()
    except Exception:
        return []

    bots = directory.get("bots", []) if isinstance(directory, dict) else directory

    claimed_result = await db.execute(
        select(GatewayAgent.exchange_account_id).where(GatewayAgent.status == "active")
    )
    claimed_ids = {row[0] for row in claimed_result.all()}

    results = []
    for bot in bots:
        bot_name = bot.get("bot_name", "")
        dev_name = bot.get("developer_name", "")
        desc = bot.get("description", "")
        if q:
            search = q.lower()
            if not (search in bot_name.lower() or search in dev_name.lower() or search in (desc or "").lower()):
                continue
        results.append(ExchangeAgentSearchResult(
            id=bot.get("id", ""),
            bot_name=bot_name,
            developer_name=dev_name,
            description=desc,
            skills=bot.get("skills", []),
            reputation=bot.get("reputation", 0.5),
            already_claimed=bot.get("id", "") in claimed_ids,
        ))

    return results


# ---- Transactions ----


@router.get("/transactions", response_model=AuditListResponse)
async def list_transactions(
    source: str | None = None,
    target: str | None = None,
    decision: PolicyDecisionType | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    import hashlib
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz
    from app.main import get_exchange_activity_cache

    audit = _gateway_components.get("audit_logger")
    entries: list = []
    total = 0
    if audit:
        entries, total = await audit.query(
            db, source_agent=source, target_agent=target, decision=decision,
            page=page, page_size=page_size,
        )

    if entries:
        return AuditListResponse(
            entries=[AuditEntryResponse.model_validate(e) for e in entries],
            total=total, page=page, page_size=page_size,
        )

    exchange_entries = get_exchange_activity_cache()
    mapped = []
    for ex in exchange_entries:
        outcome = ex.get("outcome", "approve")
        try:
            decision_val = PolicyDecisionType(outcome)
        except ValueError:
            decision_val = PolicyDecisionType.APPROVE
        ts_str = ex.get("timestamp", "")
        try:
            ts = _dt.fromisoformat(ts_str)
        except (ValueError, TypeError):
            ts = _dt.now(_tz.utc)
        src = ex.get("source_agent", "")
        tgt = ex.get("target_agent", "")
        req_hash = hashlib.sha256(f"{src}:{tgt}:{ex.get('escrow_id', '')}:{ts_str}".encode()).hexdigest()
        mapped.append(AuditEntryResponse(
            id=_uuid.UUID(ex["id"]) if ex.get("id") else _uuid.uuid4(),
            timestamp=ts,
            request_hash=req_hash,
            source_agent=src,
            target_agent=tgt,
            policy_decision=decision_val,
            escrow_id=ex.get("escrow_id"),
            latency_ms=None,
            response_status=200 if outcome == "approve" else 400,
        ))
    start = (page - 1) * page_size
    page_entries = mapped[start:start + page_size]
    return AuditListResponse(
        entries=page_entries,
        total=len(mapped), page=page, page_size=page_size,
    )


# ---- Audit ----


@router.get("/audit", response_model=AuditListResponse)
async def list_audit(
    source: str | None = None,
    target: str | None = None,
    decision: PolicyDecisionType | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    import hashlib
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz
    from app.main import get_exchange_activity_cache

    audit = _gateway_components.get("audit_logger")
    entries: list = []
    total = 0
    if audit:
        entries, total = await audit.query(
            db, source_agent=source, target_agent=target, decision=decision,
            page=page, page_size=page_size,
        )

    if entries:
        return AuditListResponse(
            entries=[AuditEntryResponse.model_validate(e) for e in entries],
            total=total, page=page, page_size=page_size,
        )

    exchange_entries = get_exchange_activity_cache()
    mapped = []
    for ex in exchange_entries:
        outcome = ex.get("outcome", "approve")
        try:
            decision_val = PolicyDecisionType(outcome)
        except ValueError:
            decision_val = PolicyDecisionType.APPROVE
        if decision and decision_val != decision:
            continue
        src = ex.get("source_agent", "")
        tgt = ex.get("target_agent", "")
        if source and source.lower() not in src.lower():
            continue
        if target and target.lower() not in tgt.lower():
            continue
        ts_str = ex.get("timestamp", "")
        try:
            ts = _dt.fromisoformat(ts_str)
        except (ValueError, TypeError):
            ts = _dt.now(_tz.utc)
        req_hash = hashlib.sha256(f"{src}:{tgt}:{ex.get('escrow_id', '')}:{ts_str}".encode()).hexdigest()
        mapped.append(AuditEntryResponse(
            id=_uuid.UUID(ex["id"]) if ex.get("id") else _uuid.uuid4(),
            timestamp=ts,
            request_hash=req_hash,
            source_agent=src,
            target_agent=tgt,
            policy_decision=decision_val,
            escrow_id=ex.get("escrow_id"),
            latency_ms=None,
            response_status=200 if outcome == "approve" else 400,
        ))
    start = (page - 1) * page_size
    page_entries = mapped[start:start + page_size]
    return AuditListResponse(
        entries=page_entries,
        total=len(mapped), page=page, page_size=page_size,
    )


@router.get("/audit/export")
async def export_audit(
    format: str = Query("json", regex="^(json|csv)$"),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    audit = _gateway_components.get("audit_logger")
    if not audit:
        return Response(content="{}", media_type="application/json")
    entries, _ = await audit.query(db, page=1, page_size=10000)
    if format == "csv":
        return Response(content=audit.export_csv(entries), media_type="text/csv")
    return Response(content=audit.export_json(entries), media_type="application/json")


# ---- Policies ----


@router.get("/policies", response_model=list[TrustPolicyResponse])
async def list_policies(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(TrustPolicy).where(TrustPolicy.active.is_(True)).order_by(TrustPolicy.updated_at.desc())
    )
    return [TrustPolicyResponse.model_validate(p) for p in result.scalars().all()]


@router.post("/policies", response_model=TrustPolicyResponse, status_code=201)
async def create_policy(
    body: TrustPolicyCreate,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.gateway.policy_engine import PolicyEngine

    valid, errors = PolicyEngine.validate_yaml(body.yaml_content)
    if not valid:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail={"errors": errors})

    existing = await db.execute(
        select(TrustPolicy).where(TrustPolicy.name == body.name)
    )
    row = existing.scalars().first()
    if row:
        row.yaml_content = body.yaml_content
        row.version += 1
        row.active = True
    else:
        row = TrustPolicy(name=body.name, yaml_content=body.yaml_content)
        db.add(row)

    await db.commit()
    await db.refresh(row)
    return TrustPolicyResponse.model_validate(row)


@router.delete("/policies/{policy_id}")
async def deactivate_policy(
    policy_id: str,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    import uuid
    from fastapi import HTTPException

    result = await db.execute(
        select(TrustPolicy).where(TrustPolicy.id == uuid.UUID(policy_id))
    )
    policy = result.scalars().first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    policy.active = False
    await db.commit()
    return {"status": "deactivated"}


@router.post("/policies/validate", response_model=PolicyValidationResult)
async def validate_policy(
    body: TrustPolicyCreate,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.gateway.policy_engine import PolicyEngine

    valid, errors = PolicyEngine.validate_yaml(body.yaml_content)
    if not valid:
        return PolicyValidationResult(valid=False, errors=errors)

    engine = _gateway_components.get("policy_engine")
    if engine:
        dry_run = await engine.dry_run(body.yaml_content, db)
        return PolicyValidationResult(valid=True, **dry_run)
    return PolicyValidationResult(valid=True)


# ---- Settlement ----


@router.get("/settlement/overview", response_model=SettlementOverviewResponse)
async def settlement_overview(_user: User = Depends(get_current_user)):
    from app.main import get_exchange_stats_cache

    startup = _gateway_components.get("startup")
    empty = SettlementOverviewResponse(
        active_escrows=0, total_locked=0, total_released=0,
        total_disputed=0, treasury_fees=0,
    )
    if not startup or not startup.exchange_client:
        return empty

    ex_stats = get_exchange_stats_cache()
    token = ex_stats.get("token_supply", {})
    activity = ex_stats.get("activity_24h", {})
    provenance = ex_stats.get("provenance", {})

    return SettlementOverviewResponse(
        active_escrows=ex_stats.get("active_escrows", 0),
        total_locked=token.get("in_escrow", 0),
        total_released=provenance.get("total_delivered", 0),
        total_disputed=provenance.get("fabrication_detected", 0),
        treasury_fees=ex_stats.get("treasury", {}).get("fees_collected", 0),
        top_agents=[],
    )


# ---- Alerts ----


@router.get("/alerts", response_model=AlertListResponse)
async def list_alerts(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    events_q = await db.execute(
        select(AlertEvent)
        .where(AlertEvent.resolved_at.is_(None))
        .order_by(AlertEvent.triggered_at.desc())
        .limit(100)
    )
    rules_q = await db.execute(
        select(AlertRule).where(AlertRule.active.is_(True))
    )
    return AlertListResponse(
        alerts=[AlertEventResponse.model_validate(e) for e in events_q.scalars().all()],
        rules=[AlertRuleResponse.model_validate(r) for r in rules_q.scalars().all()],
    )


@router.post("/alerts/rules", response_model=AlertRuleResponse, status_code=201)
async def create_alert_rule(
    body: AlertRuleCreate,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rule = AlertRule(
        name=body.name,
        condition_type=body.condition_type,
        threshold=body.threshold,
        channel=body.channel,
        agent_filter=body.agent_filter,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return AlertRuleResponse.model_validate(rule)


@router.put("/alerts/rules/{rule_id}", response_model=AlertRuleResponse)
async def update_alert_rule(
    rule_id: str,
    body: AlertRuleUpdate,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    import uuid
    from fastapi import HTTPException

    result = await db.execute(
        select(AlertRule).where(AlertRule.id == uuid.UUID(rule_id))
    )
    rule = result.scalars().first()
    if not rule:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    for field, val in body.model_dump(exclude_unset=True).items():
        setattr(rule, field, val)
    await db.commit()
    await db.refresh(rule)
    return AlertRuleResponse.model_validate(rule)


# ---- Metrics ----


@router.get("/metrics", response_model=GatewayMetricsResponse)
async def gateway_metrics(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.main import get_exchange_stats_cache

    health_mon = _gateway_components.get("health_monitor")
    rep_cache = _gateway_components.get("reputation_cache")

    agents = health_mon.get_all_agents() if health_mon else []
    local_requests = sum(a.request_count for a in agents)
    avg_lat = 0.0
    if agents:
        lats = [a.avg_latency_ms for a in agents if a.avg_latency_ms is not None]
        avg_lat = sum(lats) / len(lats) if lats else 0.0

    decisions = {}
    for decision_type in PolicyDecisionType:
        count = (
            await db.execute(
                select(func.count(AuditEntry.id)).where(
                    AuditEntry.policy_decision == decision_type
                )
            )
        ).scalar() or 0
        decisions[decision_type.value] = count

    ex_stats = get_exchange_stats_cache()
    exchange_tx = ex_stats.get("activity_24h", {}).get("transaction_count", 0)
    total_requests = local_requests + exchange_tx

    outcomes = ex_stats.get("settlement_outcomes") or {}
    if outcomes:
        decisions["approve"] += outcomes.get("released", 0)
        decisions["block"] += outcomes.get("refunded", 0)
        decisions["flag"] += outcomes.get("partial", 0)

    error_rate = 0.0
    if agents:
        error_rates = [a.error_rate for a in agents]
        error_rate = sum(error_rates) / len(error_rates)

    return GatewayMetricsResponse(
        transactions_per_hour=total_requests,
        requests_by_decision=decisions,
        error_rate=round(error_rate, 4),
        avg_latency_ms=round(avg_lat, 2),
        cache_hit_rate=rep_cache.hit_rate if rep_cache else 0.0,
    )
