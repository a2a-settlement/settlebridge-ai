"""AI-assisted deliverable review using Claude Haiku."""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

REVIEW_MODEL = "claude-haiku-4-5-20251001"

# claude-haiku-4-5 supports 200K *token* context; 400K chars ≈ 100K tokens.
# The original 12K-char limit truncated ~50% of a typical recon report, causing
# the AI reviewer to flag valid deliverables as "truncated mid-finding".
_DELIVERABLE_REVIEW_CHAR_LIMIT = 400_000

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

## Knowledge cutoff — critical rule

Your training data has a cutoff. Today's date is in 2026. Events, articles, breach \
reports, and paste dumps from 2025–2026 may be entirely real but outside your \
training data. You MUST NOT treat "I cannot verify this in my training data" as \
evidence of fabrication. Apply this distinction strictly:

- **UNVERIFIABLE** (post-cutoff URLs, recent news articles, recent breach references): \
  These require external spot-check. Set holdback=true with efficacy_criteria asking \
  the requester to verify the specific URLs within 7–14 days. Do NOT penalise the \
  score for unverifiability alone — a real recon agent will find real recent events \
  you don't know about.
- **FABRICATED** (use only when the evidence is demonstrably wrong, not merely unknown): \
  Wrong-domain URLs used as proof for a different target, URLs that point to \
  obviously unrelated content, internal contradictions (e.g. "14 wildcard certs" \
  claimed in summary but only 2 shown in findings), impossible timestamps (future \
  dates beyond today), or evidence that directly contradicts other verified facts.

## Scoring guide

- 90-100: Excellent. Meets or exceeds all criteria. Approve with full release.
- 70-89: Good. Meets most criteria, minor gaps. Approve or partial-approve.
- 50-69: Fair. Some gaps; partial approve with holdback for verification.
- 25-49: Poor. Missing major sections or contains confirmed fabrications.
- 0-24: Unacceptable. Wrong format, fundamentally flawed, or confirmed fabricated \
  evidence across multiple findings.

## General checks

- Verify all required sections are present
- Confirm output format matches what was requested
- Assess depth relative to bounty difficulty and reward
- Check for internal consistency (summary counts match finding counts)
- For security recon: evidence URLs should reference the correct target domain; \
  a cert transparency URL for netflix.com is not valid evidence for anthropic.com
- Do NOT penalise a finding simply because you cannot confirm it in your training data

Set holdback=true when findings reference recent external events or URLs that \
require spot-checking. Set efficacy_criteria to the specific URLs or claims \
to verify and the verification method (e.g. "Fetch URL and confirm content matches \
claimed article title")."""


def _build_prior_iterations_section(prior_submissions: list[dict]) -> str:
    """Render a concise history of prior submissions and their review outcomes."""
    if not prior_submissions:
        return ""

    lines = ["\n## Prior Submission History\n"]
    lines.append(
        "The agent has made prior attempts on this bounty. "
        "Award additional credit when the current submission demonstrably addresses "
        "issues flagged in earlier reviews (documented `iteration_delta`) "
        "and when scores are trending upward.\n"
    )

    for i, sub in enumerate(prior_submissions, start=1):
        review = sub.get("ai_review") or {}
        score = sub.get("score") or review.get("score")
        status = sub.get("status", "unknown")
        notes = review.get("notes", "")
        issues = review.get("issues", [])
        holdback = review.get("holdback_percent")
        submitted_at = sub.get("submitted_at", "")[:10]

        lines.append(f"### Attempt #{i}  (submitted {submitted_at}, status: {status})")
        if score is not None:
            lines.append(f"- **Score:** {score}/100")
        if holdback:
            lines.append(f"- **Holdback:** {holdback}%")
        if notes:
            lines.append(f"- **Reviewer summary:** {notes}")
        if issues:
            issues_text = "; ".join(issues[:5])
            if len(issues) > 5:
                issues_text += f" … (+{len(issues) - 5} more)"
            lines.append(f"- **Issues flagged:** {issues_text}")
        lines.append("")

    lines.append(
        "**Scoring note:** If the current submission includes an `iteration_delta` "
        "field documenting what changed relative to prior feedback, and those changes "
        "address the flagged issues above, apply a bonus of up to +10 points to the "
        "base score to reward demonstrated self-improvement.\n"
    )

    return "\n".join(lines)


def _build_prompt(
    bounty_title: str,
    bounty_description: str,
    acceptance_criteria: dict | None,
    reward_amount: int,
    difficulty: str,
    deliverable_content: str,
    provenance: dict | None,
    prior_submissions: list[dict] | None = None,
) -> str:
    parts = [
        f"## Bounty: {bounty_title}",
        f"**Reward:** {reward_amount} ATE | **Difficulty:** {difficulty}",
        f"\n### Description\n{bounty_description}",
    ]

    if acceptance_criteria:
        parts.append(f"\n### Acceptance Criteria\n{json.dumps(acceptance_criteria, indent=2)}")

    if prior_submissions:
        parts.append(_build_prior_iterations_section(prior_submissions))

    truncated = len(deliverable_content) > _DELIVERABLE_REVIEW_CHAR_LIMIT
    review_content = deliverable_content[:_DELIVERABLE_REVIEW_CHAR_LIMIT]
    if truncated:
        review_content += (
            "\n\n[SYSTEM NOTE — DO NOT PENALISE: This deliverable was truncated at "
            f"{_DELIVERABLE_REVIEW_CHAR_LIMIT:,} characters to fit the review context window. "
            f"The full deliverable is {len(deliverable_content):,} characters and is stored "
            "intact in the database. This truncation is a review-prompt artifact only. "
            "Do NOT cite apparent truncation or incomplete JSON as a defect of the submission. "
            "Evaluate the content visible above on its own merits.]"
        )
    parts.append(f"\n---\n\n## Submitted Deliverable\n\n{review_content}")

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
    prior_submissions: list[dict] | None = None,
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
        prior_submissions=prior_submissions,
    )

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=REVIEW_MODEL,
            max_tokens=2048,
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
        if prior_submissions:
            review["iteration_number"] = len(prior_submissions) + 1

        return review

    except json.JSONDecodeError as exc:
        logger.warning("AI review returned invalid JSON: %s", exc)
        return {}
    except Exception as exc:
        logger.warning("AI review failed: %s", exc)
        return {}
