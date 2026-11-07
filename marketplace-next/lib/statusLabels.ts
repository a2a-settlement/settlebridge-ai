import type {
  CyberFundingState,
  RepoStatus,
  StepState,
  WorkView,
} from "@/lib/cyberContracts";

export interface StatusTone {
  label: string;
  textClass: string;
}

export const stepLabels = {
  done: { label: "Done", textClass: "text-money-dark" },
  in_progress: { label: "In progress", textClass: "text-navy-700" },
  not_started: { label: "Not started", textClass: "text-gray-400" },
  blocked: { label: "Blocked", textClass: "text-danger" },
  inconclusive: { label: "Inconclusive", textClass: "text-warning-dark" },
  not_assessed: { label: "Not assessed", textClass: "text-gray-500" },
  needs_decision: { label: "Needs decision", textClass: "text-warning-dark" },
} as const satisfies Record<StepState, StatusTone>;

export const repoStatusLabels = {
  needs_attention: { label: "Needs attention", textClass: "text-warning-dark" },
  task_in_progress: { label: "Task in progress", textClass: "text-navy-700" },
  no_open_tasks: { label: "No open tasks", textClass: "text-gray-400" },
  paused: { label: "Paused", textClass: "text-gray-500" },
  setup_needed: { label: "Setup needed", textClass: "text-navy-700" },
  not_assessed: { label: "Not assessed", textClass: "text-gray-500" },
} as const satisfies Record<RepoStatus, StatusTone>;

export const fundingLabels = {
  balance_checked: {
    label: "Balance checked — not reserved",
    textClass: "text-navy-700",
  },
  funds_held: {
    label: "Reserved — held for active claim",
    textClass: "text-navy-700",
  },
  payment_pending: { label: "Payment pending", textClass: "text-warning-dark" },
  payment_review: {
    label: "Payment under review — funds held",
    textClass: "text-warning-dark",
  },
  paid: { label: "Paid", textClass: "text-money-dark" },
  not_paid: {
    label: "Not paid — refunded or cancelled",
    textClass: "text-gray-500",
  },
} as const satisfies Record<CyberFundingState, StatusTone>;

export const roleLabels = {
  fund: "Funder / Maintainer",
  solve: "Solver / Challenger",
} as const satisfies Record<WorkView, string>;
