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

        await db.commit()
        print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(seed())
