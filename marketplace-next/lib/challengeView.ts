// Pure view mapping for an independent challenge. No React, no new status colors.
// `done` is a recorded fact (the patch sha). no_failure_found never uses it.

import type {
  ChallengeDetail,
  ChallengeParticipant,
  FailureClass,
  Finding,
  FindingStatus,
  OracleCitation,
  StepState,
  VerdictKind,
} from "@/lib/cyberContracts";

export interface LabelledState {
  label: string;
  state: StepState;
}

export const STEP_STATE_ICONS = {
  done: "CheckCircle2",
  in_progress: "CircleDot",
  not_started: "Circle",
  blocked: "Ban",
  inconclusive: "CircleHelp",
  not_assessed: "CircleDashed",
  needs_decision: "AlertCircle",
} as const satisfies Record<StepState, string>;

export type StepIconName = (typeof STEP_STATE_ICONS)[StepState];
export type RoleIconName = StepIconName | "Wrench" | "ScanSearch" | "ShieldCheck";
export type SeverityIconName = "CircleDashed" | "BadgeAlert";
export type ChallengeIconName = RoleIconName | SeverityIconName;

export const CHALLENGE_ICON_NAMES = [
  "AlertCircle",
  "BadgeAlert",
  "Ban",
  "CheckCircle2",
  "Circle",
  "CircleDashed",
  "CircleDot",
  "CircleHelp",
  "ScanSearch",
  "ShieldCheck",
  "Wrench",
] as const satisfies readonly ChallengeIconName[];

export const NO_FAILURE_FOUND_TEXT =
  "No failure found under the stated property, environment, and budget";

export const PLATFORM_OPERATED_LABEL = "Platform-operated";

const OPERATOR_NOT_RECORDED = "Not recorded";

export const VERDICT_VIEWS = {
  confirmed: { label: "Confirmed", state: "needs_decision" },
  // A negative search result is not a recorded pass.
  no_failure_found: { label: NO_FAILURE_FOUND_TEXT, state: "not_started" },
  inconclusive: { label: "Inconclusive", state: "inconclusive" },
  invalid_or_duplicate: { label: "Invalid/Dismissed", state: "blocked" },
} as const satisfies Record<VerdictKind, LabelledState>;

export const FINDING_STATUS_VIEWS = {
  needs_decision: { label: "Needs a decision", state: "needs_decision" },
  confirmed: { label: "Confirmed", state: "needs_decision" },
  inconclusive: { label: "Inconclusive", state: "inconclusive" },
  dismissed: { label: "Invalid/Dismissed", state: "blocked" },
  superseded: { label: "Superseded", state: "not_started" },
} as const satisfies Record<FindingStatus, LabelledState>;

// The class names the claim. It is not an outcome, so it does not use done.
export const FAILURE_CLASS_VIEWS = {
  authorization_confidentiality: {
    label: "Authorization or confidentiality",
    state: "not_assessed",
  },
  integrity: { label: "Integrity", state: "not_assessed" },
  injection_execution: { label: "Injection or code execution", state: "not_assessed" },
  denial_of_service: { label: "Denial of service", state: "not_assessed" },
  regression: { label: "Regression", state: "not_assessed" },
} as const satisfies Record<FailureClass, LabelledState>;

export const ORACLE_SOURCE_LABELS = {
  property_clause: "Property clause",
  documented_rule: "Documented rule",
  existing_test: "Existing test",
  issue_text: "Issue text",
  customer_rule: "Customer rule",
} as const satisfies Record<OracleCitation["source"], string>;

export const FINDING_KIND_LABELS = {
  challenge_gap: "Challenge gap",
  historical_gap: "Historical gap",
} as const satisfies Record<Finding["kind"], string>;

const SEVERITY_LEVEL_LABELS = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
} as const satisfies Record<NonNullable<Finding["advisory_severity"]>["level"], string>;

const FINDING_STATUS_RANK = {
  needs_decision: 0,
  confirmed: 1,
  inconclusive: 2,
  dismissed: 3,
  superseded: 4,
} as const satisfies Record<FindingStatus, number>;

export const TASK_LABELS = {
  draft: "Draft",
  frozen: "Frozen",
  baselining: "Baselining",
  open: "Open",
  attempting: "Attempting",
  validating: "Validating",
  adjudicating: "Adjudicating",
  closed: "Closed",
  inconclusive: "Inconclusive",
  cancelled: "Cancelled",
} as const satisfies Record<ChallengeDetail["state"], string>;

const CHALLENGER_RUNNING = new Set<ChallengeDetail["state"]>([
  "frozen",
  "baselining",
  "open",
  "attempting",
  "validating",
]);

const VERIFIER_RUNNING = new Set<ChallengeDetail["state"]>(["attempting", "validating"]);

export function verdictView(kind: VerdictKind): LabelledState {
  return VERDICT_VIEWS[kind];
}

export function findingStatusView(status: FindingStatus): LabelledState {
  return FINDING_STATUS_VIEWS[status];
}

export function failureClassView(failureClass: FailureClass): LabelledState {
  return FAILURE_CLASS_VIEWS[failureClass];
}

export interface SeverityPresentation {
  label: string;
  rated: boolean;
  icon: SeverityIconName;
}

export function severityPresentation(
  severity: Finding["advisory_severity"]
): SeverityPresentation {
  if (severity == null) {
    return { label: "Severity not rated", rated: false, icon: "CircleDashed" };
  }
  const level = SEVERITY_LEVEL_LABELS[severity.level];
  if (!level) {
    return { label: "Severity not rated", rated: false, icon: "CircleDashed" };
  }
  const source = severity.source.trim();
  return {
    label: source ? `${level} (${source})` : level,
    rated: true,
    icon: "BadgeAlert",
  };
}

export function findingKindLabel(kind: Finding["kind"]): string {
  return FINDING_KIND_LABELS[kind] ?? "Finding";
}

export interface CitationView {
  sourceLabel: string;
  reference: string;
  quote: string;
}

export function citationView(citation: OracleCitation | null): CitationView | null {
  if (citation == null) return null;
  const known = ORACLE_SOURCE_LABELS[citation.source];
  return {
    sourceLabel: known ?? citation.source,
    reference: citation.reference.trim(),
    quote: citation.quote,
  };
}

export function sortFindings(findings: readonly Finding[]): Finding[] {
  return [...findings].sort((a, b) => {
    const left = FINDING_STATUS_RANK[a.status] ?? Number.MAX_SAFE_INTEGER;
    const right = FINDING_STATUS_RANK[b.status] ?? Number.MAX_SAFE_INTEGER;
    if (left !== right) return left - right;
    if (a.created_at !== b.created_at) return a.created_at < b.created_at ? -1 : 1;
    if (a.id !== b.id) return a.id < b.id ? -1 : 1;
    return 0;
  });
}

export function shortSha(sha: string): string {
  return sha.trim().slice(0, 7);
}

export function safeHref(value: string | null | undefined): string | null {
  if (value == null) return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (trimmed.startsWith("/") && !trimmed.startsWith("//") && !trimmed.startsWith("/\\")) {
    return trimmed;
  }
  let url: URL;
  try {
    url = new URL(trimmed);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  return url.href;
}

export function externalHref(href: string): boolean {
  return href.startsWith("http://") || href.startsWith("https://");
}

export interface RoleRowView {
  role: "solver" | "challenger" | "verifier";
  roleLabel: string;
  operatorLabel: string;
  platformOperated: boolean;
  platformLabel: string | null;
  state: StepState;
  text: string;
  icon: RoleIconName;
  reason?: string;
}

export interface FindingView {
  id: string;
  title: string;
  kindLabel: string;
  statusLabel: string;
  statusState: StepState;
  failureClassLabel: string;
  failureClassState: StepState;
  severityLabel: string;
  severityRated: boolean;
  severityIcon: SeverityIconName;
  citation: CitationView | null;
  evidenceHref: string | null;
  evidenceLabel: string;
  finding: Finding;
}

export interface ChallengeView {
  taskLabel: string;
  taskState: StepState;
  taskReason?: string;
  propertyText: string;
  scopeNote: string;
  reportHref: string | null;
  reportLabel: string | null;
  rows: RoleRowView[];
  findings: FindingView[];
}

function trimmedReason(stateReason: string | null): string | undefined {
  const reason = stateReason?.trim();
  return reason ? reason : undefined;
}

function reasonFor(state: StepState, stateReason: string | null): string | undefined {
  if (state !== "blocked" && state !== "inconclusive") return undefined;
  return trimmedReason(stateReason);
}

function operatorFor(
  challenge: ChallengeDetail,
  role: ChallengeParticipant["role"]
): Pick<RoleRowView, "operatorLabel" | "platformOperated" | "platformLabel"> {
  const match = challenge.participants.find((participant) => participant.role === role);
  if (!match) {
    return {
      operatorLabel: OPERATOR_NOT_RECORDED,
      platformOperated: false,
      platformLabel: null,
    };
  }
  const platformOperated = match.platform_operated === true;
  return {
    operatorLabel: match.operator_label.trim() || OPERATOR_NOT_RECORDED,
    platformOperated,
    platformLabel: platformOperated ? PLATFORM_OPERATED_LABEL : null,
  };
}

function solverText(targetSha: string): string {
  const sha = shortSha(targetSha);
  return sha ? `Patch submitted at ${sha}` : "Patch submitted at an unrecorded revision";
}

function challengerText(challenge: ChallengeDetail): string {
  if (challenge.summary.no_failure_found) return NO_FAILURE_FOUND_TEXT;
  return `Tried to make the patch fail: ${challenge.summary.claims_submitted} claims submitted`;
}

function checkedClaims(summary: ChallengeDetail["summary"]): number {
  // The sentence partitions checked claims into confirmed, inconclusive, and invalid.
  // claims_submitted can be larger while a claim has not reached one of those results.
  return summary.confirmed + summary.inconclusive + summary.invalid;
}

function verifierText(challenge: ChallengeDetail): string {
  const summary = challenge.summary;
  const checked = checkedClaims(summary);
  return `Independently checked ${checked} claims: ${summary.confirmed} confirmed, ${summary.inconclusive} inconclusive, ${summary.invalid} invalid`;
}

function challengerState(challenge: ChallengeDetail): StepState {
  if (challenge.summary.no_failure_found) return "not_started";
  if (challenge.state === "cancelled") return "blocked";
  if (challenge.state === "inconclusive") return "inconclusive";
  if (challenge.summary.claims_submitted === 0 || challenge.state === "draft") return "not_started";
  if (CHALLENGER_RUNNING.has(challenge.state)) return "in_progress";
  // Submitted claims are a recorded fact. The no-failure outcome never reaches this branch.
  return "done";
}

function verifierState(challenge: ChallengeDetail): StepState {
  const { summary, state } = challenge;
  if (state === "cancelled") return "blocked";
  if (summary.confirmed >= 1) return "needs_decision";
  if (state === "inconclusive" || summary.inconclusive >= 1) return "inconclusive";
  if (summary.no_failure_found) return "not_started";
  if (state === "adjudicating") return "needs_decision";
  if (VERIFIER_RUNNING.has(state)) return "in_progress";
  return "not_started";
}

function closedState(challenge: ChallengeDetail): StepState {
  const upheldOrOpen = challenge.findings.some(
    (finding) => finding.status === "needs_decision" || finding.status === "confirmed"
  );
  if (upheldOrOpen || challenge.summary.confirmed > 0) return "needs_decision";
  if (challenge.summary.inconclusive > 0 && !challenge.summary.no_failure_found) {
    return "inconclusive";
  }
  // Closed with no_failure_found stays off done. Gray "Closed" is not a pass.
  return "not_started";
}

function taskState(challenge: ChallengeDetail): StepState {
  switch (challenge.state) {
    case "draft":
      return "not_started";
    case "frozen":
    case "baselining":
    case "open":
    case "attempting":
    case "validating":
      return "in_progress";
    case "adjudicating":
      return "needs_decision";
    case "closed":
      return closedState(challenge);
    case "inconclusive":
      return "inconclusive";
    case "cancelled":
      return "blocked";
    default: {
      const unexpected: never = challenge.state;
      return unexpected;
    }
  }
}

export function evidencePresentation(url: string): { href: string | null; label: string } {
  const href = safeHref(url);
  if (href) return { href, label: "View evidence →" };
  if (url.trim()) return { href: null, label: "Evidence link is not a usable URL" };
  return { href: null, label: "No evidence link recorded" };
}

function findingView(finding: Finding): FindingView {
  const status = findingStatusView(finding.status);
  const failureClass = failureClassView(finding.failure_class);
  const severity = severityPresentation(finding.advisory_severity);
  const evidence = evidencePresentation(finding.evidence_url);
  return {
    id: finding.id,
    title: finding.title,
    kindLabel: findingKindLabel(finding.kind),
    statusLabel: status.label,
    statusState: status.state,
    failureClassLabel: failureClass.label,
    failureClassState: failureClass.state,
    severityLabel: severity.label,
    severityRated: severity.rated,
    severityIcon: severity.icon,
    citation: citationView(finding.oracle_citation),
    evidenceHref: evidence.href,
    evidenceLabel: evidence.label,
    finding,
  };
}

function reportPresentation(url: string | null): { href: string | null; label: string | null } {
  if (url == null || url.trim() === "") return { href: null, label: null };
  const href = safeHref(url);
  if (!href) return { href: null, label: "Signed report link is not a usable URL" };
  return { href, label: "View signed report →" };
}

export function buildChallengeView(challenge: ChallengeDetail): ChallengeView {
  const solverOperator = operatorFor(challenge, "solver");
  const challengerOperator = operatorFor(challenge, "challenger");
  const verifierOperator = operatorFor(challenge, "verifier");
  const challengerStep = challengerState(challenge);
  const verifierStep = verifierState(challenge);
  const taskStep = taskState(challenge);
  const report = reportPresentation(challenge.report_url);
  const taskReason = trimmedReason(challenge.state_reason);

  const rows: RoleRowView[] = [
    {
      role: "solver",
      roleLabel: "Solver",
      ...solverOperator,
      state: "done",
      text: solverText(challenge.target_sha),
      icon: "Wrench",
    },
    {
      role: "challenger",
      roleLabel: "Challenger",
      ...challengerOperator,
      state: challengerStep,
      text: challengerText(challenge),
      icon: "ScanSearch",
      reason: reasonFor(challengerStep, challenge.state_reason),
    },
    {
      role: "verifier",
      roleLabel: "Verifier",
      ...verifierOperator,
      state: verifierStep,
      text: verifierText(challenge),
      icon: challenge.summary.confirmed >= 1 ? "ShieldCheck" : STEP_STATE_ICONS[verifierStep],
      reason: reasonFor(verifierStep, challenge.state_reason),
    },
  ];

  return {
    taskLabel: TASK_LABELS[challenge.state],
    taskState: taskStep,
    taskReason,
    propertyText: challenge.property_text,
    scopeNote: challenge.scope_note,
    reportHref: report.href,
    reportLabel: report.label,
    rows,
    findings: sortFindings(challenge.findings).map(findingView),
  };
}
