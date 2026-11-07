// S0 frozen UI and API contracts for SettleBridge Cyber. Coordinator-owned:
// workers import from here and never edit it. Mirrors backend/app/schemas/challenge.py.

import type {
  CyberFundingState,
  CyberPaymentTrigger,
  CyberTask,
  CyberTaskSource,
  CyberWorkState,
} from "@/lib/types";

export type { CyberFundingState, CyberPaymentTrigger, CyberTaskSource, CyberWorkState };

// ---------------------------------------------------------------- view and context

export type WorkView = "fund" | "solve";

export interface WorkContext {
  view: WorkView;
  repo: string | null;
  task: string | null;
}

export interface RoleSwitchProps {
  value: WorkView;
  onChange: (view: WorkView) => void;
  className?: string;
}

export interface ContextBarProps {
  repo: string | null;
  task: string | null;
  taskLabel?: string | null;
  onClearRepo: () => void;
  onClearTask: () => void;
}

export interface NotSetUpPanelProps {
  view: WorkView;
  // "/dashboard/github/setup" for fund, "/dashboard/github/solver" for solve.
  href: string;
}

// ---------------------------------------------------------------- status vocabulary

export type StepState =
  | "done"
  | "in_progress"
  | "not_started"
  | "blocked"
  | "inconclusive"
  | "not_assessed"
  | "needs_decision";

export interface StatusBadgeProps {
  state: StepState;
  label: string;
  // One-line reason; required by convention for blocked and inconclusive.
  reason?: string;
  size?: "sm" | "md";
}

export type RepoStatus =
  | "needs_attention"
  | "task_in_progress"
  | "no_open_tasks"
  | "paused"
  | "setup_needed"
  | "not_assessed";

export interface RepoCardProps {
  repo: string;
  status: RepoStatus;
  reason: string;
  href: string;
  meta?: string;
}

// ---------------------------------------------------------------- task progress

export type DetailStepId = "claimed" | "patch_submitted" | "verification" | "review" | "payment";
export type FlowStepId = "alert" | "agent_repair" | "independent_challenge" | "acceptance" | "payment";

export interface ProgressStep<Id extends string = string> {
  id: Id;
  label: string;
  state: StepState;
  // Fact backing a "done" state, e.g. "Required checks passed on abc1234".
  detail?: string;
}

export interface NextAction {
  headline: string;
  sentence: string;
  link?: { label: string; href: string };
}

export interface ChecksResult {
  state: "passed" | "failed" | "pending" | "not_run";
  head_sha: string | null;
}

export type VerdictKind = "confirmed" | "no_failure_found" | "inconclusive" | "invalid_or_duplicate";

export interface CyberTaskDetail extends CyberTask {
  checks: ChecksResult;
  evaluation: "acceptable" | "needs_review" | null;
  work_expires_at: string | null;
  challenge_id: string | null;
  challenge_verdict: VerdictKind | null;
  open_findings: number;
}

export interface TaskProgressInput {
  work_state: CyberWorkState;
  funding_state: CyberFundingState;
  checks: ChecksResult;
  evaluation: "acceptable" | "needs_review" | null;
  challenge_verdict: VerdictKind | null;
  open_findings: number;
  reward_ate: number;
  work_expires_at: string | null;
  pr_url: string | null;
  bounty_id: string;
}

export interface TaskProgressModel {
  detail: ProgressStep<DetailStepId>[];
  flow: ProgressStep<FlowStepId>[];
  nextAction: NextAction;
}

export interface TaskProgressProps {
  steps: ProgressStep[];
  variant: "detailed" | "compact";
}

export interface NextActionPanelProps {
  action: NextAction;
}

// ---------------------------------------------------------------- challenge and findings

export type FailureClass =
  | "authorization_confidentiality"
  | "integrity"
  | "injection_execution"
  | "denial_of_service"
  | "regression";

export type FindingStatus = "needs_decision" | "confirmed" | "inconclusive" | "dismissed" | "superseded";

export interface OracleCitation {
  source: "property_clause" | "documented_rule" | "existing_test" | "issue_text" | "customer_rule";
  reference: string;
  quote: string;
}

export interface Finding {
  id: string;
  kind: "challenge_gap" | "historical_gap";
  repo: string;
  status: FindingStatus;
  failure_class: FailureClass;
  title: string;
  // null renders as "Severity not rated".
  advisory_severity: { level: "critical" | "high" | "medium" | "low"; source: string } | null;
  oracle_citation: OracleCitation | null;
  target_sha: string;
  evidence_url: string;
  created_at: string;
}

export interface ChallengeParticipant {
  role: "author" | "solver" | "challenger" | "verifier" | "adjudicator";
  operator_label: string;
  platform_operated: boolean;
}

export interface ChallengeDetail {
  id: string;
  repo: string;
  pr_number: number | null;
  base_sha: string;
  target_sha: string;
  state:
    | "draft"
    | "frozen"
    | "baselining"
    | "open"
    | "attempting"
    | "validating"
    | "adjudicating"
    | "closed"
    | "inconclusive"
    | "cancelled";
  state_reason: string | null;
  property_text: string;
  participants: ChallengeParticipant[];
  summary: {
    claims_submitted: number;
    confirmed: number;
    inconclusive: number;
    invalid: number;
    no_failure_found: boolean;
  };
  verdicts: {
    claim_id: string | null;
    verdict: VerdictKind;
    decided_by: "machine" | "adjudicator";
    reason: string;
    created_at: string;
  }[];
  findings: Finding[];
  report_url: string | null;
  scope_note: string;
}

export interface ChallengeResultProps {
  challenge: ChallengeDetail;
}

export interface FindingCardProps {
  finding: Finding;
}

// ---------------------------------------------------------------- leases (P0-F1)

export interface MyLease {
  lease_id: string;
  bounty_id: string;
  repo: string;
  issue_number: number;
  status: "work" | "submitted" | "released" | "expired" | "settled";
  work_expires_at: string;
  pr_number: number | null;
  evaluation: "acceptable" | "needs_review" | null;
  payout: "paid" | "pending" | "not_paid" | null;
  amount_ate: number;
}

export const CHECKS_CAPTION =
  "Checks show the configured tests passed. They do not show the code is secure.";
