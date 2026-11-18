"""Read imported CVE reach rankings for the fix-verifier queue.

``FIX_VERIFIER_PRIORITY_QUEUE`` gates both lookups. Nothing in the request
path calls these yet; verifications still run inline.
"""

from __future__ import annotations

from sqlalchemy import nulls_last, select

from app.config import settings
from app.models.fix_verifier import FixVerification, FixVerificationState
from app.models.reach import CvePriority


async def priority_for(db, vuln_id: str) -> CvePriority | None:
    """Return the imported ranking for ``vuln_id``, or None when the flag is off."""
    if not settings.FIX_VERIFIER_PRIORITY_QUEUE:
        return None
    result = await db.execute(select(CvePriority).where(CvePriority.cve == vuln_id))
    return result.scalar_one_or_none()


async def queued_by_priority(db, limit: int = 50) -> list[FixVerification]:
    """Queued verifications, oldest first unless the priority flag is on.

    With the flag on, higher ``cve_priority.priority`` comes first. A missing
    ranking and a NULL priority both sort after every real score (PostgreSQL
    puts NULLs first on DESC unless NULLS LAST is set), then by ``created_at``.
    """
    queued = FixVerification.state == FixVerificationState.QUEUED.value
    stmt = select(FixVerification).where(queued)
    if settings.FIX_VERIFIER_PRIORITY_QUEUE:
        stmt = stmt.outerjoin(CvePriority, CvePriority.cve == FixVerification.vuln_id).order_by(
            nulls_last(CvePriority.priority.desc()),
            FixVerification.created_at.asc(),
        )
    else:
        stmt = stmt.order_by(FixVerification.created_at.asc())
    result = await db.execute(stmt.limit(limit))
    return list(result.scalars().all())
