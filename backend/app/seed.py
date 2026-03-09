"""Seed the database with categories and example bounties."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import settings
from app.database import async_session, engine, Base
from app.middleware.auth import hash_password
from app.models.bounty import Bounty, BountyStatus, Difficulty, ProvenanceTier
from app.models.category import Category
from app.models.assist_session import AssistSession, AssistSessionStatus
from app.models.contract import ContractStatus, ServiceContract
from app.models.snapshot import Snapshot, SnapshotStatus
from app.models.user import User, UserType

CATEGORIES = [
    ("Web Research", "web-research", "Research tasks across the web", "Globe"),
    ("Lead Enrichment", "lead-enrichment", "Enrich contact and company data", "Users"),
    ("Document Extraction", "document-extraction", "Extract structured data from documents", "FileText"),
    ("Code Generation", "code-generation", "Generate code from specifications", "Code"),
    ("Code Review / Fix", "code-review-fix", "Review and fix existing code", "Bug"),
    ("Data Labeling / Structuring", "data-labeling-structuring", "Label and structure datasets", "Database"),
    ("Summarization", "summarization", "Summarize documents and data", "AlignLeft"),
    ("Market Research", "market-research", "Research markets and competitors", "TrendingUp"),
    ("Compliance / Legal Research", "compliance-legal-research", "Research compliance and legal topics", "Shield"),
    ("Content Generation", "content-generation", "Generate marketing and editorial content", "PenTool"),
    ("API Integration", "api-integration", "Build integrations between APIs", "Plug"),
    ("Data Analysis", "data-analysis", "Analyze datasets and produce insights", "BarChart"),
]


async def seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as db:
        # Categories
        existing = (await db.execute(select(Category))).scalars().all()
        if not existing:
            cat_map = {}
            for i, (name, slug, desc, icon) in enumerate(CATEGORIES):
                cat = Category(name=name, slug=slug, description=desc, icon=icon, sort_order=i)
                db.add(cat)
                cat_map[slug] = cat
            await db.flush()
            print(f"Seeded {len(CATEGORIES)} categories")
        else:
            print("Categories already exist, skipping")
            cat_map = {c.slug: c for c in existing}

        # Test users
        existing_user = (
            await db.execute(select(User).where(User.email == "requester@example.com"))
        ).scalar_one_or_none()

        if not existing_user:
            requester = User(
                email="requester@example.com",
                password_hash=hash_password("password123"),
                display_name="Demo Requester",
                user_type=UserType.REQUESTER,
            )
            agent = User(
                email="agent@example.com",
                password_hash=hash_password("password123"),
                display_name="Demo Agent",
                user_type=UserType.AGENT_OPERATOR,
            )
            db.add(requester)
            db.add(agent)
            await db.flush()
            print("Seeded 2 test users")
        else:
            requester = existing_user
            print("Test users already exist, skipping")

        # Bounties
        existing_bounties = (await db.execute(select(Bounty))).scalars().all()
        if not existing_bounties:
            now = datetime.now(timezone.utc)
            bounties_data = [
                {
                    "title": "Research latest SEC 10-K filing for NVIDIA and extract risk factors",
                    "description": "Pull the most recent SEC 10-K filing for NVIDIA (NVDA) and extract all risk factors into a structured format. Each risk factor should include a category, description, and direct source URL to the filing section.",
                    "category_slug": "market-research",
                    "reward_amount": 150,
                    "provenance_tier": ProvenanceTier.TIER2_SIGNED,
                    "difficulty": Difficulty.MEDIUM,
                    "acceptance_criteria": {
                        "description": "JSON output with risk_factors array",
                        "output_format": "json",
                        "required_sources": ["SEC EDGAR"],
                        "provenance_tier": "tier2_signed",
                        "custom_checks": [
                            {"field": "risk_factors", "type": "array", "min_length": 5}
                        ],
                    },
                },
                {
                    "title": "Scrape and structure the speaker list from RSA Conference 2026",
                    "description": "Extract the complete speaker list from RSA Conference 2026. Structure the data as CSV with columns: name, title, company, session_title, session_time.",
                    "category_slug": "data-labeling-structuring",
                    "reward_amount": 75,
                    "provenance_tier": ProvenanceTier.TIER2_SIGNED,
                    "difficulty": Difficulty.EASY,
                    "acceptance_criteria": {
                        "description": "CSV with speaker data",
                        "output_format": "csv",
                        "required_sources": ["rsaconference.com"],
                        "provenance_tier": "tier2_signed",
                    },
                },
                {
                    "title": "Generate a Python FastAPI middleware for rate limiting with Redis backend",
                    "description": "Create a production-ready FastAPI middleware that implements rate limiting backed by Redis. Must support per-route configuration, sliding window algorithm, and include comprehensive docstrings.",
                    "category_slug": "code-generation",
                    "reward_amount": 100,
                    "provenance_tier": ProvenanceTier.TIER1_SELF_DECLARED,
                    "difficulty": Difficulty.MEDIUM,
                    "acceptance_criteria": {
                        "description": "Working Python code with tests and docstrings",
                        "output_format": "code",
                        "provenance_tier": "tier1_self_declared",
                    },
                },
                {
                    "title": "Summarize all NIST AI RMF publications from 2025-2026",
                    "description": "Produce a comprehensive summary of all NIST AI Risk Management Framework publications from 2025 through 2026. Include publication title, date, key findings, and direct source URLs.",
                    "category_slug": "compliance-legal-research",
                    "reward_amount": 300,
                    "provenance_tier": ProvenanceTier.TIER3_VERIFIABLE,
                    "difficulty": Difficulty.HARD,
                    "acceptance_criteria": {
                        "description": "Markdown report with citations",
                        "output_format": "markdown",
                        "required_sources": ["nist.gov"],
                        "provenance_tier": "tier3_verifiable",
                    },
                },
                {
                    "title": "Enrich this list of 50 defense contractors with CEO name, revenue, and CAGE code",
                    "description": "Given a list of 50 defense contractors, enrich each row with the current CEO name, latest annual revenue, and CAGE code. All sources must be cited per row.",
                    "category_slug": "lead-enrichment",
                    "reward_amount": 200,
                    "provenance_tier": ProvenanceTier.TIER2_SIGNED,
                    "difficulty": Difficulty.MEDIUM,
                    "acceptance_criteria": {
                        "description": "Completed CSV with all fields populated and sources cited",
                        "output_format": "csv",
                        "provenance_tier": "tier2_signed",
                    },
                },
            ]

            for bd in bounties_data:
                cat = cat_map.get(bd.pop("category_slug"))
                bounty = Bounty(
                    requester_id=requester.id,
                    status=BountyStatus.OPEN,
                    category_id=cat.id if cat else None,
                    deadline=now + timedelta(days=14),
                    tags=[cat.slug] if cat else [],
                    **bd,
                )
                db.add(bounty)

            await db.flush()
            print("Seeded 5 example bounties")
        else:
            print("Bounties already exist, skipping")

        # Service contracts
        existing_contracts = (await db.execute(select(ServiceContract))).scalars().all()
        if not existing_contracts:
            agent_q = await db.execute(
                select(User).where(User.email == "agent@example.com")
            )
            agent = agent_q.scalar_one_or_none()
            if agent and requester:
                now = datetime.now(timezone.utc)
                contract = ServiceContract(
                    requester_id=requester.id,
                    agent_user_id=agent.id,
                    agent_exchange_bot_id=agent.exchange_bot_id or "demo-agent-bot",
                    title="Weekly GovCon Market Intelligence Report",
                    description="Produce a structured weekly report on government contracting activity: new RFPs, contract awards, key agency budget signals, and upcoming events.",
                    category_id=cat_map.get("market-research", next(iter(cat_map.values()))).id if cat_map else None,
                    acceptance_criteria={
                        "description": "Markdown report with at least 5 RFPs, 5 awards, and 3 events",
                        "output_format": "markdown",
                        "required_sources": ["sam.gov", "fpds.gov"],
                    },
                    provenance_tier=ProvenanceTier.TIER2_SIGNED,
                    reward_per_snapshot=100,
                    schedule="0 9 * * 1",
                    schedule_description="Every Monday at 9:00 AM UTC",
                    max_snapshots=12,
                    grace_period_hours=24,
                    auto_approve=True,
                    status=ContractStatus.ACTIVE,
                    group_id=f"contract-{uuid.uuid4().hex[:12]}",
                    activated_at=now - timedelta(days=21),
                )
                db.add(contract)
                await db.flush()

                for i in range(3):
                    cycle = i + 1
                    due = now - timedelta(days=21 - (i * 7))
                    snap = Snapshot(
                        contract_id=contract.id,
                        cycle_number=cycle,
                        status=SnapshotStatus.APPROVED if cycle <= 2 else SnapshotStatus.PENDING,
                        due_at=due,
                        deadline_at=due + timedelta(hours=24),
                        delivered_at=due + timedelta(hours=3) if cycle <= 2 else None,
                        approved_at=due + timedelta(hours=4) if cycle <= 2 else None,
                        deliverable={
                            "content": f"# GovCon Weekly Report — Week {cycle}\n\n## New RFPs\n- RFP-{1000+cycle}: Cloud Migration Services (DoD)\n- RFP-{2000+cycle}: Cybersecurity Assessment (DHS)\n\n## Contract Awards\n- $4.2M to TechCorp for AI/ML services\n\n## Upcoming Events\n- AFCEA TechNet conference — next Thursday",
                            "content_type": "text/markdown",
                        } if cycle <= 2 else None,
                        provenance={
                            "source_type": "aggregated",
                            "source_refs": ["sam.gov", "fpds.gov"],
                            "content_hash": f"demo-hash-{cycle}",
                        } if cycle <= 2 else None,
                    )
                    db.add(snap)

                await db.flush()
                print("Seeded 1 example service contract with 3 snapshot cycles")
        else:
            print("Service contracts already exist, skipping")

        # Example Bounty Assist session
        existing_sessions = (await db.execute(select(AssistSession))).scalars().all()
        if not existing_sessions:
            now_str = datetime.now(timezone.utc).isoformat()
            session = AssistSession(
                user_id=requester.id,
                status=AssistSessionStatus.DRAFT_READY,
                turn_count=3,
                messages=[
                    {
                        "role": "user",
                        "content": "I want to know if the market is going to crash.",
                        "timestamp": now_str,
                    },
                    {
                        "role": "assistant",
                        "content": "That's a question a lot of people are asking right now. To turn this into a bounty that attracts serious analytical intelligence, I need to understand a few things:\n\n1. **What's your context?** Are you managing a portfolio and considering hedging, timing an entry point, or evaluating real estate exposure?\n2. **What's your time horizon?** Are you worried about the next 3 months, 12 months, or a longer structural shift?\n\nThis will help me shape the bounty so analysts know exactly what kind of analysis you need.",
                        "timestamp": now_str,
                    },
                    {
                        "role": "user",
                        "content": "I manage a retirement portfolio, mostly index funds. I'm worried about the next 12 months. I want to know if I should move to bonds or stay put.",
                        "timestamp": now_str,
                    },
                    {
                        "role": "assistant",
                        "content": "Perfect, that gives me a clear picture. You need a decision-grade analysis, not just a prediction. Let me shape the deliverable:\n\n**What the bounty should ask for:**\n- A multi-factor regime analysis assessing the probability of a 20%+ S&P 500 drawdown within 12 months\n- Leading indicators and trigger mechanisms identified\n- Sector-specific vulnerability ranking\n- An actionable recommendation framework for a retirement-stage index portfolio\n\n**Settlement structure:** Since this is a predictive bounty, I'd recommend a tiered payout:\n- **45% immediate** for the analytical framework and indicator identification (this has standalone value regardless of what the market does)\n- **35% performance tranche** held in escrow for 12 months, released based on the accuracy of the leading indicators the analyst identifies\n- **Reputation stake** weighted at 1.5x for long-duration outcome accuracy\n\nDoes this structure feel right? I'd suggest a reward of 2,000-5,000 ATE given the complexity and predictive component.",
                        "timestamp": now_str,
                    },
                    {
                        "role": "user",
                        "content": "That sounds great. Let's go with 3000 ATE.",
                        "timestamp": now_str,
                    },
                    {
                        "role": "assistant",
                        "content": "Excellent. Here's your complete bounty draft:\n\n**Title:** Multi-Factor S&P 500 Drawdown Probability Analysis (12-Month Horizon)\n\n**Description:** Provide a multi-factor regime analysis assessing the probability of a 20%+ S&P 500 drawdown within 12 months, including leading indicator identification, trigger mechanism analysis, and sector-specific vulnerability ranking. Must include an actionable recommendation framework for a retirement-stage index fund portfolio, with confidence scoring and full methodology disclosure.\n\nThe bounty is ready to post. Review it in the panel and click Post Bounty when you're ready.",
                        "timestamp": now_str,
                    },
                ],
                bounty_draft={
                    "title": "Multi-Factor S&P 500 Drawdown Probability Analysis (12-Month Horizon)",
                    "description": "Provide a multi-factor regime analysis assessing the probability of a 20%+ S&P 500 drawdown within 12 months, including leading indicator identification, trigger mechanism analysis, and sector-specific vulnerability ranking. Must include an actionable recommendation framework for a retirement-stage index fund portfolio, with confidence scoring and full methodology disclosure.",
                    "category_slug": "market-research",
                    "tags": ["market-crash", "s&p-500", "portfolio", "risk-analysis", "predictive"],
                    "acceptance_criteria": {
                        "description": "Multi-factor regime analysis with probability assessment, leading indicators, trigger mechanisms, sector vulnerability ranking, and portfolio recommendation framework. Must include confidence intervals and methodology disclosure.",
                        "output_format": "markdown",
                        "required_sources": ["Federal Reserve data", "market data providers", "economic indicators"],
                        "provenance_tier": "tier2_signed",
                    },
                    "reward_suggestion": 3000,
                    "difficulty": "expert",
                    "provenance_tier": "tier2_signed",
                    "deadline_suggestion": "30 days",
                    "settlement_structure": {
                        "immediate_payout_percent": 45,
                        "performance_tranches": [
                            {
                                "percent": 35,
                                "indicator": "Accuracy of analyst-identified leading indicators over 12-month observation period",
                                "measurement": "observable_market_data",
                                "escrow_duration_days": 365,
                                "partial_credit": True,
                            }
                        ],
                        "reputation_stake": {"enabled": True, "weight": 1.5},
                    },
                },
                settlement_structure={
                    "immediate_payout_percent": 45,
                    "performance_tranches": [
                        {
                            "percent": 35,
                            "indicator": "Accuracy of analyst-identified leading indicators over 12-month observation period",
                            "measurement": "observable_market_data",
                            "escrow_duration_days": 365,
                            "partial_credit": True,
                        }
                    ],
                    "reputation_stake": {"enabled": True, "weight": 1.5},
                },
            )
            db.add(session)
            await db.flush()
            print("Seeded 1 example Bounty Assist session")
        else:
            print("Assist sessions already exist, skipping")

        await db.commit()
        print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(seed())
