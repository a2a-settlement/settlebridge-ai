"""Management API routes for the SettleBridge Gateway."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.models.gateway import (
    AlertEvent,
    AlertRule,
    AuditEntry,
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
    health_mon = _gateway_components.get("health_monitor")
    startup = _gateway_components.get("startup")

    agents = health_mon.get_all_agents() if health_mon else []
    active_count = sum(1 for a in agents if a.status == "active")

    total_tx = (await db.execute(select(func.count(AuditEntry.id)))).scalar() or 0
    violations = (
        await db.execute(
            select(func.count(AuditEntry.id)).where(
                AuditEntry.policy_decision == PolicyDecisionType.BLOCK
            )
        )
    ).scalar() or 0

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
    audit = _gateway_components.get("audit_logger")
    if not audit:
        return AuditListResponse(entries=[], total=0, page=page, page_size=page_size)
    entries, total = await audit.query(
        db, source_agent=source, target_agent=target, decision=decision,
        page=page, page_size=page_size,
    )
    return AuditListResponse(
        entries=[AuditEntryResponse.model_validate(e) for e in entries],
        total=total, page=page, page_size=page_size,
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
    audit = _gateway_components.get("audit_logger")
    if not audit:
        return AuditListResponse(entries=[], total=0, page=page, page_size=page_size)
    entries, total = await audit.query(
        db, source_agent=source, target_agent=target, decision=decision,
        page=page, page_size=page_size,
    )
    return AuditListResponse(
        entries=[AuditEntryResponse.model_validate(e) for e in entries],
        total=total, page=page, page_size=page_size,
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
    startup = _gateway_components.get("startup")
    if not startup or not startup.exchange_client:
        return SettlementOverviewResponse(
            active_escrows=0, total_locked=0, total_released=0,
            total_disputed=0, treasury_fees=0,
        )
    try:
        client = startup.exchange_client
        escrows = client.list_escrows(status="active")
        active = escrows.get("escrows", [])
        return SettlementOverviewResponse(
            active_escrows=len(active),
            total_locked=sum(e.get("amount", 0) for e in active),
            total_released=0,
            total_disputed=0,
            treasury_fees=0,
        )
    except Exception:
        return SettlementOverviewResponse(
            active_escrows=0, total_locked=0, total_released=0,
            total_disputed=0, treasury_fees=0,
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
    health_mon = _gateway_components.get("health_monitor")
    rep_cache = _gateway_components.get("reputation_cache")

    agents = health_mon.get_all_agents() if health_mon else []
    total_requests = sum(a.request_count for a in agents)
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
