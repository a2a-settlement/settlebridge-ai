// Runs with `npm run test:unit` (node --test, built-in type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import type {
  ChecksResult,
  CyberFundingState,
  CyberWorkState,
  ProgressStep,
  TaskProgressInput,
  TaskProgressModel,
  VerdictKind,
  WorkView,
} from "@/lib/cyberContracts";
import {
  buildTaskProgress,
  taskProgressFundingStates,
  taskProgressViews,
  taskProgressWorkStates,
} from "../lib/taskProgress.ts";

const WORK_STATES = [
  "available",
  "claimed",
  "pr_submitted",
  "evaluating",
  "needs_maintainer_review",
  "awaiting_merge",
  "settling",
  "completed",
  "cancelled",
] as const satisfies readonly CyberWorkState[];

const FUNDING_STATES = [
  "balance_checked",
  "funds_held",
  "payment_pending",
  "payment_review",
  "paid",
  "not_paid",
] as const satisfies readonly CyberFundingState[];

const VIEWS = ["fund", "solve"] as const satisfies readonly WorkView[];

const CHECK_STATES = ["passed", "failed", "pending", "not_run"] as const satisfies readonly ChecksResult["state"][];

const VERDICTS = [
  null,
  "confirmed",
  "no_failure_found",
  "inconclusive",
  "invalid_or_duplicate",
] as const satisfies readonly (VerdictKind | null)[];

const FINDING_COUNTS = [0, 1, 2] as const;

const STEP_STATES = new Set([
  "done",
  "in_progress",
  "not_started",
  "blocked",
  "inconclusive",
  "not_assessed",
  "needs_decision",
]);

const DETAIL_IDS = ["claimed", "patch_submitted", "verification", "review", "payment"];
const FLOW_IDS = ["alert", "agent_repair", "independent_challenge", "acceptance", "payment"];
const DETAIL_LABELS = ["Claimed", "Patch submitted", "Verification", "Review", "Payment"];
const FLOW_LABELS = ["Alert", "Agent repair", "Independent challenge", "Acceptance", "Payment"];

const PAST_CLAIM = new Set<CyberWorkState>([
  "pr_submitted",
  "evaluating",
  "needs_maintainer_review",
  "awaiting_merge",
  "settling",
  "completed",
]);

const PAST_PATCH = new Set<CyberWorkState>([
  "evaluating",
  "needs_maintainer_review",
  "awaiting_merge",
  "settling",
  "completed",
]);

const HEAD = "0123456789abcdef0123456789abcdef01234567";
const NO_FAILURE =
  "No failure found under the stated property, environment, and budget";
const NOT_ASSESSED = "Independent challenge: Not assessed";
const REWARD = 42;
const BOUNTY_ID = "task-1";
const PR_URL = "https://github.com/acme/widget/pull/3";
const EXPIRES = "2026-04-05T06:07:08+00:00";

const MAIN_CASES =
  WORK_STATES.length *
  FUNDING_STATES.length *
  VIEWS.length *
  CHECK_STATES.length *
  VERDICTS.length *
  FINDING_COUNTS.length;

function shortSha(head: string | null): string | null {
  if (!head) return null;
  const trimmed = head.trim();
  return trimmed ? trimmed.slice(0, 7) : null;
}

function findings(count: number): number {
  if (!Number.isFinite(count) || count <= 0) return 0;
  return Math.floor(count);
}

function settlementFunding(state: CyberFundingState): boolean {
  return state === "paid" || state === "payment_pending" || state === "payment_review";
}

function progressedPastPatch(input: TaskProgressInput): boolean {
  return (
    input.checks.state === "passed" ||
    input.checks.state === "failed" ||
    input.checks.state === "pending" ||
    input.evaluation !== null ||
    findings(input.open_findings) > 0 ||
    settlementFunding(input.funding_state)
  );
}

function claimMayBeDone(input: TaskProgressInput): boolean {
  if (PAST_CLAIM.has(input.work_state)) return true;
  if (input.work_state !== "cancelled") return false;
  if (input.pr_url?.trim()) return true;
  return progressedPastPatch(input);
}

function patchMayBeDone(input: TaskProgressInput): boolean {
  if (PAST_PATCH.has(input.work_state)) return true;
  if (input.work_state !== "cancelled") return false;
  return progressedPastPatch(input);
}

function verificationMayBeDone(input: TaskProgressInput): boolean {
  if (input.checks.state !== "passed" || !shortSha(input.checks.head_sha)) return false;
  if (input.work_state === "evaluating") return false;
  if (
    input.work_state === "needs_maintainer_review" ||
    input.work_state === "awaiting_merge" ||
    input.work_state === "settling" ||
    input.work_state === "completed"
  ) {
    return true;
  }
  return input.work_state === "cancelled" && progressedPastPatch(input);
}

function reviewMayBeDone(input: TaskProgressInput): boolean {
  if (findings(input.open_findings) > 0) return false;
  if (input.work_state === "settling" || input.work_state === "completed") return true;
  return input.work_state === "cancelled" && settlementFunding(input.funding_state);
}

function paymentMayBeDone(input: TaskProgressInput): boolean {
  return input.funding_state === "paid" && input.work_state === "completed";
}

function stepById(steps: ProgressStep[], id: string): ProgressStep {
  const step = steps.find((item) => item.id === id);
  assert.ok(step, id);
  return step;
}

function stringsOf(value: unknown, found: string[]): void {
  if (typeof value === "string") {
    found.push(value);
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) stringsOf(item, found);
    return;
  }
  if (value && typeof value === "object") {
    for (const item of Object.values(value)) stringsOf(item, found);
  }
}

function tag(input: TaskProgressInput, view: WorkView): string {
  return [
    view,
    input.work_state,
    input.funding_state,
    input.checks.state,
    input.checks.head_sha ? "sha" : "no-sha",
    String(input.challenge_verdict),
    `findings=${input.open_findings}`,
    `evaluation=${input.evaluation}`,
    input.pr_url ? "pr" : "no-pr",
    input.work_expires_at ? "expires" : "no-expires",
  ].join(" ");
}

function inputOf(fields: Partial<TaskProgressInput> & Pick<TaskProgressInput, "work_state" | "funding_state">): TaskProgressInput {
  return {
    work_state: fields.work_state,
    funding_state: fields.funding_state,
    checks: fields.checks ?? { state: "not_run", head_sha: null },
    evaluation: fields.evaluation ?? null,
    challenge_verdict: fields.challenge_verdict ?? null,
    open_findings: fields.open_findings ?? 0,
    reward_ate: fields.reward_ate ?? REWARD,
    work_expires_at: fields.work_expires_at ?? null,
    pr_url: fields.pr_url ?? null,
    bounty_id: fields.bounty_id ?? BOUNTY_ID,
  };
}

function assertCopy(model: TaskProgressModel, input: TaskProgressInput, view: WorkView, label: string): void {
  const action = model.nextAction;
  if (input.work_state === "cancelled") {
    assert.equal(action.headline, "Cancelled", label);
    assert.equal(action.sentence, "This task was cancelled.", label);
    assert.equal(action.link, undefined, label);
    return;
  }
  if (input.funding_state === "not_paid") {
    assert.equal(action.headline, "Not paid", label);
    assert.equal(action.sentence, "Not paid — refunded or cancelled.", label);
    assert.equal(action.link, undefined, label);
    return;
  }
  if (input.funding_state === "payment_review") {
    assert.equal(action.headline, "Payment under review", label);
    assert.equal(
      action.sentence,
      "Payment under review — funds held. GitHub has not confirmed the alert is fixed.",
      label,
    );
    assert.equal(action.link, undefined, label);
    return;
  }
  if (input.work_state === "completed" && input.funding_state === "paid") {
    assert.equal(action.headline, "Paid", label);
    assert.equal(action.sentence, `Paid ${input.reward_ate} ATE (test).`, label);
    assert.deepEqual(action.link, { label: "View ledger entry →", href: "/dashboard/wallet" }, label);
    return;
  }
  if (input.work_state === "needs_maintainer_review") {
    if (view === "solve") {
      assert.equal(action.headline, "Waiting for maintainer review", label);
      assert.equal(action.sentence, "Nothing is needed from you.", label);
      assert.equal(action.link, undefined, label);
      return;
    }
    assert.equal(action.headline, "Awaiting maintainer review", label);
    const open = findings(input.open_findings);
    if (open > 0) {
      assert.equal(
        action.sentence,
        open === 1
          ? "One challenger finding needs a decision."
          : `${open} challenger findings need a decision.`,
        label,
      );
      assert.deepEqual(
        action.link,
        { label: "Review finding →", href: `/bounties/${encodeURIComponent(input.bounty_id)}` },
        label,
      );
      return;
    }
    const sha = shortSha(input.checks.head_sha);
    if (input.checks.state === "passed" && sha) {
      assert.equal(
        action.sentence,
        "Required checks passed. The policy assessment needs your review.",
        label,
      );
    } else if (input.checks.state === "failed") {
      assert.equal(
        action.sentence,
        "Required checks failed. The policy assessment needs your review.",
        label,
      );
    } else {
      assert.equal(
        action.sentence,
        "Required checks have not passed. The policy assessment needs your review.",
        label,
      );
    }
    if (input.pr_url) {
      assert.deepEqual(action.link, { label: "Review pull request →", href: input.pr_url }, label);
    } else {
      assert.equal(action.link, undefined, label);
    }
    return;
  }
  if (input.work_state === "evaluating") {
    const sha = shortSha(input.checks.head_sha);
    assert.equal(action.headline, "Checks running", label);
    assert.equal(
      action.sentence,
      sha
        ? `Checks are running on ${sha}. Nothing is needed now.`
        : "Checks are running. Nothing is needed now.",
      label,
    );
    assert.equal(action.link, undefined, label);
    return;
  }
  if (input.work_state === "claimed" && view === "solve") {
    const deadline = input.work_expires_at?.trim();
    assert.equal(action.headline, "Open a pull request", label);
    assert.equal(
      action.sentence,
      deadline ? `Open a pull request before ${deadline}.` : "Open a pull request.",
      label,
    );
    assert.deepEqual(
      action.link,
      { label: "View task terms →", href: `/bounties/${encodeURIComponent(input.bounty_id)}` },
      label,
    );
  }
}

function assertModel(model: TaskProgressModel, input: TaskProgressInput, view: WorkView): void {
  const label = tag(input, view);
  assert.deepEqual(model.detail.map((step) => step.id), DETAIL_IDS, label);
  assert.deepEqual(model.flow.map((step) => step.id), FLOW_IDS, label);
  assert.deepEqual(model.detail.map((step) => step.label), DETAIL_LABELS, label);
  assert.deepEqual(model.flow.map((step) => step.label), FLOW_LABELS, label);

  const claimed = stepById(model.detail, "claimed");
  const patch = stepById(model.detail, "patch_submitted");
  const verification = stepById(model.detail, "verification");
  const review = stepById(model.detail, "review");
  const payment = stepById(model.detail, "payment");
  const alert = stepById(model.flow, "alert");
  const repair = stepById(model.flow, "agent_repair");
  const challenge = stepById(model.flow, "independent_challenge");
  const acceptance = stepById(model.flow, "acceptance");
  const flowPayment = stepById(model.flow, "payment");

  for (const step of [...model.detail, ...model.flow]) {
    assert.ok(STEP_STATES.has(step.state), `${label} ${step.id} ${step.state}`);
    if (step.state === "done") {
      assert.ok(step.detail && step.detail.trim().length > 0, `${label} ${step.id} missing fact`);
    }
  }

  assert.equal(alert.state, "done", label);
  assert.equal(acceptance.state, review.state, label);
  assert.equal(flowPayment.state, payment.state, label);
  if (repair.state === "done") {
    assert.equal(claimed.state, "done", label);
    assert.equal(patch.state, "done", label);
  }

  const doneAllowed: Record<string, boolean> = {
    alert: true,
    claimed: claimMayBeDone(input),
    patch_submitted: patchMayBeDone(input),
    verification: verificationMayBeDone(input),
    review: reviewMayBeDone(input),
    payment: paymentMayBeDone(input),
    agent_repair: claimMayBeDone(input) && patchMayBeDone(input),
    independent_challenge:
      input.challenge_verdict === "confirmed" && findings(input.open_findings) === 0,
    acceptance: reviewMayBeDone(input),
  };
  for (const step of [...model.detail, ...model.flow]) {
    if (step.state !== "done") continue;
    assert.equal(doneAllowed[step.id], true, `${label} ${step.id} done without a recorded fact`);
  }

  if (verification.state === "done") {
    const sha = shortSha(input.checks.head_sha);
    assert.ok(sha, label);
    assert.ok(verification.detail?.includes(`Required checks passed on ${sha}`), label);
  }
  if (payment.state === "done") {
    assert.ok(payment.detail?.includes(`Paid ${input.reward_ate} ATE (test)`), label);
  }

  if (input.challenge_verdict === "no_failure_found") {
    assert.equal(challenge.state, "not_started", label);
    assert.equal(challenge.detail, NO_FAILURE, label);
  } else if (input.challenge_verdict === null) {
    assert.equal(challenge.state, "not_assessed", label);
  } else if (input.challenge_verdict === "confirmed" && findings(input.open_findings) > 0) {
    assert.equal(challenge.state, "needs_decision", label);
  } else if (input.challenge_verdict === "confirmed") {
    assert.equal(challenge.state, "done", label);
  } else {
    assert.equal(challenge.state, "inconclusive", label);
  }
  if (input.challenge_verdict === "no_failure_found") {
    assert.notEqual(challenge.state, "done", label);
  }

  const terminal = input.work_state === "cancelled" || input.funding_state === "not_paid";
  const reason =
    input.work_state === "cancelled"
      ? "Cancelled"
      : input.funding_state === "not_paid"
        ? "Not paid — refunded or cancelled"
        : null;
  let furthest = -1;
  model.detail.forEach((step, index) => {
    if (step.state !== "not_started") furthest = index;
  });
  if (input.challenge_verdict === null) {
    const verificationIndex = model.detail.findIndex((step) => step.id === "verification");
    const wiped = terminal && furthest >= 0 && verificationIndex > furthest;
    const replaced =
      terminal && furthest === verificationIndex && verification.state === "blocked";
    if (!wiped && !replaced) {
      assert.ok(verification.detail?.includes(NOT_ASSESSED), label);
    }
  }
  if (terminal && reason) {
    if (furthest < 0) {
      assert.ok(model.detail.every((step) => step.state === "not_started"), label);
    } else {
      assert.equal(model.detail[furthest]?.state, "blocked", label);
      assert.equal(model.detail[furthest]?.detail, reason, label);
      for (let index = furthest + 1; index < model.detail.length; index += 1) {
        assert.equal(model.detail[index]?.state, "not_started", label);
        assert.equal(model.detail[index]?.detail, undefined, label);
      }
    }
  }

  if (input.work_state === "evaluating") {
    const blob = JSON.stringify(model);
    assert.equal(blob.includes("Required checks passed"), false, label);
    if (!terminal) assert.equal(verification.state, "in_progress", label);
  }
  if (
    (input.work_state === "needs_maintainer_review" || input.work_state === "awaiting_merge") &&
    !terminal
  ) {
    if (input.checks.state === "passed" && shortSha(input.checks.head_sha)) {
      assert.equal(verification.state, "done", label);
    } else if (input.checks.state === "failed") {
      assert.equal(verification.state, "blocked", label);
    } else if (input.checks.state === "pending") {
      assert.equal(verification.state, "in_progress", label);
    } else {
      assert.equal(verification.state, "not_started", label);
    }
    if (findings(input.open_findings) > 0) assert.equal(review.state, "needs_decision", label);
    else assert.equal(review.state, "in_progress", label);
  }

  const passedPhraseOk =
    input.checks.state === "passed" &&
    shortSha(input.checks.head_sha) !== null &&
    input.work_state !== "evaluating";
  const blob = JSON.stringify(model);
  if (!passedPhraseOk) assert.equal(blob.includes("Required checks passed"), false, label);
  if (!paymentMayBeDone(input)) assert.equal(/Paid \d/.test(blob), false, label);

  const texts: string[] = [];
  stringsOf(model, texts);
  for (const text of texts) {
    assert.equal(text.toLowerCase().includes("secure"), false, `${label} ${text}`);
  }

  assert.ok(model.nextAction.headline.trim(), label);
  assert.ok(model.nextAction.sentence.trim(), label);
  if (model.nextAction.link) {
    const href = model.nextAction.link.href;
    assert.ok(model.nextAction.link.label.trim(), label);
    assert.ok(
      (href.startsWith("/") && !href.startsWith("//")) ||
        href.startsWith("https://") ||
        href.startsWith("http://"),
      label,
    );
  }
  assertCopy(model, input, view, label);
}

test("progress axes cover every contract state", () => {
  assert.deepEqual([...taskProgressWorkStates].sort(), [...WORK_STATES].sort());
  assert.deepEqual([...taskProgressFundingStates].sort(), [...FUNDING_STATES].sort());
  assert.deepEqual([...taskProgressViews].sort(), [...VIEWS].sort());
});

test("every work, funding, and view combination has backed progress", () => {
  let seen = 0;
  for (const work_state of WORK_STATES) {
    for (const funding_state of FUNDING_STATES) {
      for (const view of VIEWS) {
        for (const checks of CHECK_STATES) {
          for (const challenge_verdict of VERDICTS) {
            for (const open_findings of FINDING_COUNTS) {
              const input = inputOf({
                work_state,
                funding_state,
                checks: { state: checks, head_sha: HEAD },
                challenge_verdict,
                open_findings,
              });
              assertModel(buildTaskProgress(input, view), input, view);
              seen += 1;
            }
          }
        }
      }
    }
  }
  assert.equal(seen, MAIN_CASES);
});

test("a passed check without a head is not done", () => {
  for (const work_state of WORK_STATES) {
    for (const funding_state of FUNDING_STATES) {
      for (const view of VIEWS) {
        const input = inputOf({
          work_state,
          funding_state,
          checks: { state: "passed", head_sha: null },
        });
        const model = buildTaskProgress(input, view);
        assert.notEqual(stepById(model.detail, "verification").state, "done", tag(input, view));
        assertModel(model, input, view);
      }
    }
  }
});

test("cancelled progress follows recorded claim and pull request facts", () => {
  const evaluations = [null, "acceptable", "needs_review"] as const;
  const deadlines = [null, EXPIRES] as const;
  const pulls = [null, PR_URL] as const;
  for (const funding_state of FUNDING_STATES) {
    for (const view of VIEWS) {
      for (const evaluation of evaluations) {
        for (const work_expires_at of deadlines) {
          for (const pr_url of pulls) {
            const input = inputOf({
              work_state: "cancelled",
              funding_state,
              evaluation,
              work_expires_at,
              pr_url,
              checks: { state: "not_run", head_sha: null },
            });
            assertModel(buildTaskProgress(input, view), input, view);
          }
        }
      }
    }
  }
});

test("buildTaskProgress does not mutate its input", () => {
  const input = inputOf({
    work_state: "needs_maintainer_review",
    funding_state: "funds_held",
    checks: { state: "passed", head_sha: HEAD },
    pr_url: PR_URL,
    open_findings: 2,
    challenge_verdict: "confirmed",
  });
  const before = structuredClone(input);
  const first = buildTaskProgress(input, "fund");
  const second = buildTaskProgress(input, "fund");
  assert.deepEqual(input, before);
  assert.deepEqual(first, second);
  assert.equal(first.nextAction.sentence, "2 challenger findings need a decision.");
  assert.equal(first.nextAction.link?.label, "Review finding →");
});
