import type {
  ChecksResult,
  CyberFundingState,
  CyberWorkState,
  DetailStepId,
  FlowStepId,
  NextAction,
  ProgressStep,
  StepState,
  TaskProgressInput,
  TaskProgressModel,
  VerdictKind,
  WorkView,
} from "@/lib/cyberContracts";

const DETAIL_LABEL: Record<DetailStepId, string> = {
  claimed: "Claimed",
  patch_submitted: "Patch submitted",
  verification: "Verification",
  review: "Review",
  payment: "Payment",
};

const FLOW_LABEL: Record<FlowStepId, string> = {
  alert: "Alert",
  agent_repair: "Agent repair",
  independent_challenge: "Independent challenge",
  acceptance: "Acceptance",
  payment: "Payment",
};

const NOT_ASSESSED = "Independent challenge: Not assessed";
const NO_FAILURE =
  "No failure found under the stated property, environment, and budget";
const CANCELLED = "Cancelled";
const NOT_PAID = "Not paid — refunded or cancelled";

const WORK_AXIS = {
  available: true,
  claimed: true,
  pr_submitted: true,
  evaluating: true,
  needs_maintainer_review: true,
  awaiting_merge: true,
  settling: true,
  completed: true,
  cancelled: true,
} as const satisfies Record<CyberWorkState, true>;

const FUNDING_AXIS = {
  balance_checked: true,
  funds_held: true,
  payment_pending: true,
  payment_review: true,
  paid: true,
  not_paid: true,
} as const satisfies Record<CyberFundingState, true>;

const VIEW_AXIS = {
  fund: true,
  solve: true,
} as const satisfies Record<WorkView, true>;

export const taskProgressWorkStates: readonly CyberWorkState[] = Object.keys(
  WORK_AXIS,
) as CyberWorkState[];

export const taskProgressFundingStates: readonly CyberFundingState[] = Object.keys(
  FUNDING_AXIS,
) as CyberFundingState[];

export const taskProgressViews: readonly WorkView[] = Object.keys(VIEW_AXIS) as WorkView[];

type Stage = Exclude<CyberWorkState, "cancelled">;

const REVIEW_STAGES = new Set<Stage>(["needs_maintainer_review", "awaiting_merge"]);
const SETTLEMENT_STAGES = new Set<Stage>(["settling", "completed"]);

function clean(value: string | null | undefined): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function shortSha(head: string | null): string | null {
  const value = clean(head);
  if (!value) return null;
  return value.slice(0, 7);
}

function findingCount(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 0;
  return Math.floor(value);
}

function findingsSentence(count: number): string {
  const findings = findingCount(count);
  if (findings === 1) return "One challenger finding needs a decision.";
  return `${findings} challenger findings need a decision.`;
}

function paidDetail(amount: number): string {
  return `Paid ${amount} ATE (test)`;
}

function detailStep(
  id: DetailStepId,
  state: StepState,
  detail?: string,
): ProgressStep<DetailStepId> {
  const step: ProgressStep<DetailStepId> = { id, label: DETAIL_LABEL[id], state };
  if (detail) step.detail = detail;
  return step;
}

function settlementFunding(state: CyberFundingState): boolean {
  return state === "paid" || state === "payment_pending" || state === "payment_review";
}

function inferredStage(input: TaskProgressInput): Stage {
  if (settlementFunding(input.funding_state)) return "settling";
  if (
    input.evaluation !== null ||
    findingCount(input.open_findings) > 0 ||
    input.checks.state === "passed" ||
    input.checks.state === "failed"
  ) {
    return "needs_maintainer_review";
  }
  if (input.checks.state === "pending") return "evaluating";
  if (clean(input.pr_url)) return "pr_submitted";
  if (clean(input.work_expires_at)) return "claimed";
  return "available";
}

function stageOf(input: TaskProgressInput): Stage {
  if (input.work_state === "cancelled") return inferredStage(input);
  return input.work_state;
}

function withAssessment(detail: string | undefined, verdict: VerdictKind | null): string | undefined {
  if (verdict !== null) return detail;
  if (!detail) return NOT_ASSESSED;
  if (detail.includes(NOT_ASSESSED)) return detail;
  return `${detail.replace(/\.$/, "")}. ${NOT_ASSESSED}`;
}

function runningDetail(checks: ChecksResult): string {
  const sha = shortSha(checks.head_sha);
  return sha ? `Checks are running on ${sha}` : "Checks are running";
}

function verificationStep(input: TaskProgressInput, stage: Stage): ProgressStep<DetailStepId> {
  const verdict = input.challenge_verdict;
  if (stage === "evaluating") {
    return detailStep("verification", "in_progress", withAssessment(runningDetail(input.checks), verdict));
  }
  const reached =
    REVIEW_STAGES.has(stage) || SETTLEMENT_STAGES.has(stage);
  if (!reached) {
    return detailStep("verification", "not_started", withAssessment(undefined, verdict));
  }
  if (input.checks.state === "passed") {
    const sha = shortSha(input.checks.head_sha);
    if (sha) {
      return detailStep(
        "verification",
        "done",
        withAssessment(`Required checks passed on ${sha}`, verdict),
      );
    }
    return detailStep(
      "verification",
      "not_started",
      withAssessment("Required checks have no recorded head", verdict),
    );
  }
  if (input.checks.state === "failed") {
    return detailStep(
      "verification",
      "blocked",
      withAssessment("Required checks failed", verdict),
    );
  }
  if (input.checks.state === "pending") {
    return detailStep("verification", "in_progress", withAssessment(runningDetail(input.checks), verdict));
  }
  return detailStep(
    "verification",
    "not_started",
    withAssessment("Checks have not run", verdict),
  );
}

function reviewStep(input: TaskProgressInput, stage: Stage): ProgressStep<DetailStepId> {
  if (stage === "available" || stage === "claimed" || stage === "pr_submitted" || stage === "evaluating") {
    return detailStep("review", "not_started");
  }
  const findings = findingCount(input.open_findings);
  if (findings > 0) {
    // An open finding is still undecided, so review is not a recorded completion.
    return detailStep("review", "needs_decision", findingsSentence(findings));
  }
  if (REVIEW_STAGES.has(stage)) return detailStep("review", "in_progress");
  if (stage === "completed") return detailStep("review", "done", "Review completed");
  return detailStep("review", "done", "Settlement started");
}

function paymentStep(input: TaskProgressInput, stage: Stage): ProgressStep<DetailStepId> {
  if (input.funding_state === "paid" && input.work_state === "completed") {
    return detailStep("payment", "done", paidDetail(input.reward_ate));
  }
  if (!SETTLEMENT_STAGES.has(stage)) return detailStep("payment", "not_started");
  if (input.funding_state === "payment_review") {
    return detailStep("payment", "in_progress", "Payment under review — funds held");
  }
  return detailStep("payment", "in_progress", "Payment pending");
}

function claimedStep(stage: Stage): ProgressStep<DetailStepId> {
  if (stage === "available") return detailStep("claimed", "not_started");
  if (stage === "claimed") return detailStep("claimed", "in_progress");
  return detailStep("claimed", "done", "Claim recorded");
}

function patchStep(stage: Stage): ProgressStep<DetailStepId> {
  if (stage === "available" || stage === "claimed") return detailStep("patch_submitted", "not_started");
  if (stage === "pr_submitted") return detailStep("patch_submitted", "in_progress");
  return detailStep("patch_submitted", "done", "Pull request submitted");
}

function terminalReason(input: TaskProgressInput): string | null {
  if (input.work_state === "cancelled") return CANCELLED;
  if (input.funding_state === "not_paid") return NOT_PAID;
  return null;
}

function applyTerminal(
  steps: ProgressStep<DetailStepId>[],
  reason: string | null,
): ProgressStep<DetailStepId>[] {
  if (!reason) return steps;
  let furthest = -1;
  for (let index = 0; index < steps.length; index += 1) {
    const step = steps[index];
    if (step && step.state !== "not_started") furthest = index;
  }
  if (furthest < 0) return steps;
  return steps.map((step, index) => {
    if (index < furthest) return step;
    if (index === furthest) return { ...step, state: "blocked", detail: reason };
    return { id: step.id, label: step.label, state: "not_started" };
  });
}

function detailSteps(input: TaskProgressInput): ProgressStep<DetailStepId>[] {
  const stage = stageOf(input);
  return applyTerminal(
    [
      claimedStep(stage),
      patchStep(stage),
      verificationStep(input, stage),
      reviewStep(input, stage),
      paymentStep(input, stage),
    ],
    terminalReason(input),
  );
}

function repairStep(
  claimed: ProgressStep<DetailStepId>,
  patch: ProgressStep<DetailStepId>,
): ProgressStep<FlowStepId> {
  const label = FLOW_LABEL.agent_repair;
  if (claimed.state === "done" && patch.state === "done") {
    return {
      id: "agent_repair",
      label,
      state: "done",
      detail: "Claim recorded. Pull request submitted.",
    };
  }
  if (claimed.state === "blocked" || patch.state === "blocked") {
    const source = patch.state === "blocked" ? patch : claimed;
    return { id: "agent_repair", label, state: "blocked", detail: source.detail };
  }
  if (claimed.state === "not_started" && patch.state === "not_started") {
    return { id: "agent_repair", label, state: "not_started" };
  }
  return { id: "agent_repair", label, state: "in_progress" };
}

function challengeStep(input: TaskProgressInput): ProgressStep<FlowStepId> {
  const label = FLOW_LABEL.independent_challenge;
  const verdict = input.challenge_verdict;
  if (verdict === null) {
    return { id: "independent_challenge", label, state: "not_assessed", detail: "Not assessed" };
  }
  if (verdict === "no_failure_found") {
    // A negative result under the stated budget is not a completed check.
    return { id: "independent_challenge", label, state: "not_started", detail: NO_FAILURE };
  }
  if (verdict === "confirmed") {
    if (findingCount(input.open_findings) > 0) {
      return {
        id: "independent_challenge",
        label,
        state: "needs_decision",
        detail: findingsSentence(input.open_findings),
      };
    }
    return {
      id: "independent_challenge",
      label,
      state: "done",
      detail: "Confirmed finding decided",
    };
  }
  if (verdict === "inconclusive") {
    return {
      id: "independent_challenge",
      label,
      state: "inconclusive",
      detail: "Inconclusive under the stated property, environment, and budget.",
    };
  }
  return {
    id: "independent_challenge",
    label,
    state: "inconclusive",
    detail: "Invalid or duplicate under the stated property, environment, and budget.",
  };
}

function mirror(
  id: FlowStepId,
  source: ProgressStep<DetailStepId>,
): ProgressStep<FlowStepId> {
  return { id, label: FLOW_LABEL[id], state: source.state, detail: source.detail };
}

function flowSteps(
  detail: ProgressStep<DetailStepId>[],
  input: TaskProgressInput,
): ProgressStep<FlowStepId>[] {
  const claimed = detail.find((step) => step.id === "claimed");
  const patch = detail.find((step) => step.id === "patch_submitted");
  const review = detail.find((step) => step.id === "review");
  const payment = detail.find((step) => step.id === "payment");
  if (!claimed || !patch || !review || !payment) {
    throw new Error("task progress detail is missing a step");
  }
  return [
    { id: "alert", label: FLOW_LABEL.alert, state: "done", detail: "Alert recorded" },
    repairStep(claimed, patch),
    challengeStep(input),
    mirror("acceptance", review),
    mirror("payment", payment),
  ];
}

function bountyHref(bountyId: string): string {
  return `/bounties/${encodeURIComponent(bountyId)}`;
}

function safeHref(value: string | null | undefined): string | null {
  const href = clean(value);
  if (!href) return null;
  if (href.startsWith("/") && !href.startsWith("//")) return href;
  if (href.startsWith("https://") || href.startsWith("http://")) return href;
  return null;
}

function withLink(action: NextAction, label: string, href: string | null): NextAction {
  if (!href) return action;
  return { ...action, link: { label, href } };
}

function passedFact(checks: ChecksResult): boolean {
  return checks.state === "passed" && shortSha(checks.head_sha) !== null;
}

function solveClaimed(input: TaskProgressInput): NextAction {
  const deadline = clean(input.work_expires_at);
  return {
    headline: "Open a pull request",
    sentence: deadline ? `Open a pull request before ${deadline}.` : "Open a pull request.",
    link: { label: "View task terms →", href: bountyHref(input.bounty_id) },
  };
}

function maintainerReview(input: TaskProgressInput, view: WorkView): NextAction {
  if (view === "solve") {
    return {
      headline: "Waiting for maintainer review",
      sentence: "Nothing is needed from you.",
    };
  }
  const findings = findingCount(input.open_findings);
  if (findings > 0) {
    return withLink(
      {
        headline: "Awaiting maintainer review",
        sentence: findingsSentence(findings),
      },
      "Review finding →",
      bountyHref(input.bounty_id),
    );
  }
  const sentence = passedFact(input.checks)
    ? "Required checks passed. The policy assessment needs your review."
    : input.checks.state === "failed"
      ? "Required checks failed. The policy assessment needs your review."
      : "Required checks have not passed. The policy assessment needs your review.";
  return withLink(
    { headline: "Awaiting maintainer review", sentence },
    "Review pull request →",
    safeHref(input.pr_url),
  );
}

function evaluatingCopy(input: TaskProgressInput): NextAction {
  const sha = shortSha(input.checks.head_sha);
  return {
    headline: "Checks running",
    sentence: sha
      ? `Checks are running on ${sha}. Nothing is needed now.`
      : "Checks are running. Nothing is needed now.",
  };
}

function paidCopy(input: TaskProgressInput): NextAction {
  return {
    headline: "Paid",
    sentence: `${paidDetail(input.reward_ate)}.`,
    link: { label: "View ledger entry →", href: "/dashboard/wallet" },
  };
}

function paymentReviewCopy(): NextAction {
  return {
    headline: "Payment under review",
    sentence:
      "Payment under review — funds held. GitHub has not confirmed the alert is fixed.",
  };
}

function fallback(input: TaskProgressInput, view: WorkView): NextAction {
  const pr = safeHref(input.pr_url);
  switch (input.work_state) {
    case "available":
      if (view === "solve") {
        return {
          headline: "Claim this task",
          sentence: "Claim the task to start work.",
          link: { label: "View task terms →", href: bountyHref(input.bounty_id) },
        };
      }
      return {
        headline: "Waiting for a solver",
        sentence: "This task is open. No claim has been recorded.",
      };
    case "claimed":
      if (view === "solve") return solveClaimed(input);
      return {
        headline: "Solver is working",
        sentence: "A solver has claimed this task. No pull request has been recorded.",
      };
    case "pr_submitted":
      if (view === "solve") {
        return {
          headline: "Patch submitted",
          sentence: "Your pull request is open. Nothing is needed until checks finish.",
        };
      }
      return withLink(
        {
          headline: "Patch submitted",
          sentence: passedFact(input.checks)
            ? "A pull request is open. Required checks passed."
            : input.checks.state === "failed"
              ? "A pull request is open. Required checks failed."
              : "A pull request is open. Required checks have not been recorded as passed.",
        },
        "Review pull request →",
        pr,
      );
    case "evaluating":
      return evaluatingCopy(input);
    case "needs_maintainer_review":
      return maintainerReview(input, view);
    case "awaiting_merge":
      if (view === "solve") {
        return { headline: "Waiting for merge", sentence: "Nothing is needed from you." };
      }
      return withLink(
        {
          headline: "Awaiting merge",
          sentence: passedFact(input.checks)
            ? "Required checks passed. The pull request is waiting for merge."
            : "The pull request is waiting for merge. Required checks have not passed.",
        },
        "Review pull request →",
        pr,
      );
    case "settling":
      if (input.funding_state === "payment_pending") {
        return {
          headline: "Payment pending",
          sentence: "Settlement is in progress. Payment is pending and has not been recorded as paid.",
        };
      }
      return {
        headline: "Settlement in progress",
        sentence: "Settlement is in progress. Payment has not been recorded as paid.",
      };
    case "completed":
      if (input.funding_state === "paid") return paidCopy(input);
      if (input.funding_state === "payment_pending") {
        return {
          headline: "Payment pending",
          sentence: "This task is completed. Payment is pending and has not been recorded as paid.",
        };
      }
      return {
        headline: "Completed",
        sentence: "This task is completed. Payment has not been recorded as paid.",
      };
    case "cancelled":
      return { headline: "Cancelled", sentence: "This task was cancelled." };
    default: {
      const unreachable: never = input.work_state;
      throw new Error(`unknown work state ${String(unreachable)}`);
    }
  }
}

function nextAction(input: TaskProgressInput, view: WorkView): NextAction {
  if (input.work_state === "cancelled") {
    return { headline: "Cancelled", sentence: "This task was cancelled." };
  }
  if (input.funding_state === "not_paid") {
    return { headline: "Not paid", sentence: `${NOT_PAID}.` };
  }
  if (input.funding_state === "payment_review") return paymentReviewCopy();
  if (input.work_state === "completed" && input.funding_state === "paid") return paidCopy(input);
  if (input.work_state === "needs_maintainer_review") return maintainerReview(input, view);
  if (input.work_state === "evaluating") return evaluatingCopy(input);
  if (input.work_state === "claimed" && view === "solve") return solveClaimed(input);
  return fallback(input, view);
}

export function buildTaskProgress(input: TaskProgressInput, view: WorkView): TaskProgressModel {
  const detail = detailSteps(input);
  return {
    detail,
    flow: flowSteps(detail, input),
    nextAction: nextAction(input, view),
  };
}
