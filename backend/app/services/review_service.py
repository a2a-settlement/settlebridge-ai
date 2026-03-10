"""AI-assisted deliverable review using Claude Haiku."""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

REVIEW_MODEL = "claude-haiku-4-5-20251001"

REVIEW_SYSTEM = """\
You are a deliverable reviewer for an escrow-backed bounty marketplace. \
Your job is to evaluate whether a submitted deliverable meets the bounty's \
acceptance criteria. Be fair but rigorous.

You MUST respond with valid JSON matching this schema exactly:
{
  "score": <int 0-100>,
  "recommendation": "<approve | partial_approve | reject>",
  "holdback": <bool>,
  "holdback_percent": <int 0-99, only if holdback is true>,
  "efficacy_criteria": "<string, only if holdback is true>",
  "issues": ["<list of specific issues found>"],
  "notes": "<2-4 sentence reviewer summary>"
}

Scoring guide:
- 90-100: Excellent. Meets or exceeds all criteria. Approve with full release.
- 70-89: Good. Meets most criteria, minor gaps. Consider partial approval with holdback.
- 50-69: Fair. Meets some criteria but has notable gaps. Partial approve with significant holdback.
- 25-49: Poor. Fails to meet key criteria. Reject.
- 0-24: Unacceptable. Off-topic, wrong format, or fundamentally flawed. Reject.

When checking deliverables:
- Verify all required sections/components are present
- Check that dates, numbers, and facts are plausible and current (today's year is 2026)
- Confirm output format matches what was requested
- Assess depth and quality relative to the bounty's difficulty and reward
- Flag any hallucinated, outdated, or contradictory content

Set holdback=true when the deliverable looks good but contains predictions or \
claims that can only be verified after some time has passed. Set efficacy_criteria \
to describe what should be checked and when."""


def _build_prompt(
    bounty_title: str,
    bounty_description: str,
    acceptance_criteria: dict | None,
    reward_amount: int,
    difficulty: str,
    deliverable_content: str,
    provenance: dict | None,
) -> str:
    parts = [
        f"## Bounty: {bounty_title}",
        f"**Reward:** {reward_amount} ATE | **Difficulty:** {difficulty}",
        f"\n### Description\n{bounty_description}",
    ]

    if acceptance_criteria:
        parts.append(f"\n### Acceptance Criteria\n{json.dumps(acceptance_criteria, indent=2)}")

    parts.append(f"\n---\n\n## Submitted Deliverable\n\n{deliverable_content[:12000]}")

    if provenance:
        parts.append(f"\n### Provenance\n{json.dumps(provenance, indent=2)}")

    parts.append("\n---\n\nEvaluate this deliverable against the bounty requirements. Respond with JSON only.")

    return "\n".join(parts)


async def review_deliverable(
    bounty_title: str,
    bounty_description: str,
    acceptance_criteria: dict | None,
    reward_amount: int,
    difficulty: str,
    deliverable_content: str,
    provenance: dict | None = None,
) -> dict[str, Any]:
    """Run AI review of a deliverable. Returns the assessment dict."""

    if not settings.ANTHROPIC_API_KEY:
        logger.warning("No ANTHROPIC_API_KEY configured, skipping AI review")
        return {}

    prompt = _build_prompt(
        bounty_title=bounty_title,
        bounty_description=bounty_description,
        acceptance_criteria=acceptance_criteria,
        reward_amount=reward_amount,
        difficulty=difficulty,
        deliverable_content=deliverable_content,
        provenance=provenance,
    )

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=REVIEW_MODEL,
            max_tokens=1024,
            system=REVIEW_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        review = json.loads(text)

        required_keys = {"score", "recommendation", "holdback", "notes"}
        if not required_keys.issubset(review.keys()):
            logger.warning("AI review missing keys: %s", required_keys - review.keys())
            return {}

        review["score"] = max(0, min(100, int(review["score"])))
        review["model"] = REVIEW_MODEL

        return review

    except json.JSONDecodeError as exc:
        logger.warning("AI review returned invalid JSON: %s", exc)
        return {}
    except Exception as exc:
        logger.warning("AI review failed: %s", exc)
        return {}
