// Pure mapping for the funder dashboard. No I/O.
// No signed-in GET returns alert-cap usage or a repository paused flag.
// Headroom is setup-status `funder.headroom_ate`, then ledger `free_ate`.

import type { RepoStatus, WorkView } from "@/lib/cyberContracts";

// Same order as sortRepoCards in components/RepoCard.tsx (frozen). The
// dashboard sorts rendered cards with sortRepoCards; this copy is for tests.
export const REPO_STATUS_ORDER: readonly RepoStatus[] = [
  "needs_attention",
  "setup_needed",
  "task_in_progress",
  "paused",
  "not_assessed",
  "no_open_tasks",
];

const REPO_NAME = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

const DECISION_STATES = new Set(["needs_maintainer_review", "needs_review"]);

const OPEN_STATES = new Set([
  "available",
  "open",
  "claimed",
  "pr_submitted",
  "evaluating",
  "settling",
  "submitted",
  "advertised",
  "reserved",
  "in_review",
]);

const CLOSED_STATES = new Set([
  "completed",
  "cancelled",
  "paid",
  "not_paid",
  "refunded",
  "expired",
  "draft",
  "released",
  "settled",
]);

const ALERT_SOURCES = new Set(["dependabot", "code_scanning"]);

export interface FunderTask {
  bountyId: string;
  title: string;
  workState: string;
  openFindings: number | null;
  source: string | null;
}

export interface RepoFacts {
  repo: string;
  appInstalled: boolean | null;
  linkEnabled: boolean | null;
  paused: boolean | null;
  tasksKnown: boolean;
  tasks: readonly FunderTask[];
  openFindings: number | null;
  bountyCount: number | null;
  alertCount: number | null;
  autoMergeAcceptable: boolean | null;
}

export interface RepoAssessment {
  status: RepoStatus;
  reason: string;
  meta: string;
}

export interface OwnedBounty {
  bountyId: string;
  title: string;
  repo: string | null;
  bountyStatus: string | null;
  ledgerState: string | null;
}

export interface CyberHit {
  bountyId: string;
  title: string;
  repo: string;
  workState: string;
  source: string | null;
  openFindings: number | null;
}

export interface SetupSnapshot {
  repoAccess: boolean | null;
  enabled: boolean | null;
  autoMergeAcceptable: boolean | null;
  headroomAte: number | null;
  minimumAte: number | null;
}

export interface SetupRead {
  appInstalled: boolean | null;
  linkEnabled: boolean | null;
  autoMergeAcceptable: boolean | null;
  headroomAte: number | null;
  minimumAte: number | null;
}

export interface ApprovalTask {
  bountyId: string;
  title: string;
  repo: string;
  workState: string;
  autoMergeAcceptable: boolean | null;
}

export interface FunderLedgerRow {
  bountyId: string;
  repo: string;
  issueNumber: number;
  title: string;
  state: string;
  amountAte: number | null;
  feeAte: number | null;
}

export interface BudgetNumbers {
  headroomAte: number | null;
  minimumAte: number | null;
}

export interface FunderRepoCard {
  repo: string;
  status: RepoStatus;
  reason: string;
  meta: string;
}

export interface FunderModelInput {
  contextRepo: string | null;
  // Repos the caller funds. Null when the list could not be loaded.
  linked?: readonly string[] | null;
  ownedKnown: boolean;
  owned: readonly OwnedBounty[];
  cyber: readonly CyberHit[] | null;
  cyberComplete: boolean;
  setups: readonly { repo: string; denied: boolean; snapshot: SetupSnapshot | null }[];
  ledgerFreeAte: number | null;
  ledgerExchangeFailed: boolean;
}

export interface FunderModel {
  repos: FunderRepoCard[];
  approvals: ApprovalTask[];
  budget: BudgetNumbers;
  tasksIncomplete: boolean;
  discoveryComplete: boolean;
}

export function isRepoName(value: unknown): value is string {
  if (typeof value !== "string" || !REPO_NAME.test(value)) return false;
  const slash = value.indexOf("/");
  const owner = value.slice(0, slash);
  const name = value.slice(slash + 1);
  return owner !== "." && owner !== ".." && name !== "." && name !== "..";
}

export function uniqueRepos(names: readonly (string | null | undefined)[]): string[] {
  const seen = new Set<string>();
  const repos: string[] = [];
  for (const name of names) {
    if (!isRepoName(name)) continue;
    const key = name.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    repos.push(name);
  }
  return repos;
}

export function repoFromTags(tags: readonly string[] | null | undefined): string | null {
  if (!tags || !tags.some((tag) => tag.toLowerCase() === "github")) return null;
  for (const tag of tags) {
    if (tag.toLowerCase() !== "github" && isRepoName(tag)) return tag;
  }
  return null;
}

export function setupStatusPath(repo: string): string | null {
  if (!isRepoName(repo)) return null;
  const slash = repo.indexOf("/");
  const owner = repo.slice(0, slash);
  const name = repo.slice(slash + 1);
  if (owner === "." || owner === ".." || name === "." || name === "..") return null;
  return `/v1/github/repos/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/setup-status`;
}

export function roleIncludesView(userType: string, view: WorkView): boolean {
  if (view === "fund") return userType === "requester" || userType === "both";
  if (view === "solve") return userType === "agent_operator" || userType === "both";
  return false;
}

export function attentionReason(count: number): string {
  const noun = count === 1 ? "task needs" : "tasks need";
  return `Needs attention: ${count} ${noun} your review`;
}

export function formatAteTest(amount: number): string {
  if (typeof amount !== "number" || !Number.isFinite(amount)) return "ATE (test)";
  const negative = amount < 0;
  const digits = String(Math.trunc(Math.abs(amount)));
  const withCommas = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}${withCommas} ATE (test)`;
}

export function readLinkedRepos(data: unknown): string[] | null {
  if (!data || typeof data !== "object") return null;
  const repos = (data as { repos?: unknown }).repos;
  if (!Array.isArray(repos)) return null;
  const names: string[] = [];
  for (const entry of repos) {
    const name = entry && typeof entry === "object" ? (entry as { repo?: unknown }).repo : null;
    if (typeof name === "string" && isRepoName(name)) names.push(name);
  }
  return names;
}

export function readOpenFindings(record: object): number | null {
  if (!("open_findings" in record)) return null;
  const value = (record as { open_findings?: unknown }).open_findings;
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return null;
  return Math.trunc(value);
}

export function ledgerStateToWorkState(state: string): string | null {
  switch (state.trim().toLowerCase()) {
    case "advertised":
      return "available";
    case "reserved":
      return "claimed";
    case "settling":
      return "settling";
    case "paid":
      return "completed";
    case "refunded":
    case "cancelled":
      return "cancelled";
    default:
      return null;
  }
}

export function bountyStatusToWorkState(status: string): string | null {
  switch (status.trim().toLowerCase()) {
    case "open":
      return "available";
    case "claimed":
      return "claimed";
    case "submitted":
      return "pr_submitted";
    case "completed":
      return "completed";
    case "cancelled":
    case "expired":
      return "cancelled";
    case "draft":
      return "draft";
    default:
      return null;
  }
}

function finiteOrNull(value: number | null | undefined): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value;
}

function countPhrase(count: number | null, one: string, many: string): string {
  if (count === null || !Number.isFinite(count) || count < 0) {
    const label = many.charAt(0).toUpperCase() + many.slice(1);
    return `${label} not assessed`;
  }
  const whole = Math.trunc(count);
  return `${whole} ${whole === 1 ? one : many}`;
}

export function repoMeta(bountyCount: number | null, alertCount: number | null): string {
  return `${countPhrase(bountyCount, "bounty", "bounties")} · ${countPhrase(alertCount, "alert", "alerts")}`;
}

function findingsNeedDecision(task: FunderTask): boolean {
  return typeof task.openFindings === "number" && task.openFindings > 0;
}

function isDecisionTask(task: FunderTask, autoMerge: boolean | null): boolean {
  if (DECISION_STATES.has(task.workState)) return true;
  if (task.workState === "awaiting_merge" && autoMerge === false) return true;
  return findingsNeedDecision(task);
}

function isOpenTask(task: FunderTask, autoMerge: boolean | null): boolean {
  if (isDecisionTask(task, autoMerge)) return false;
  if (task.workState === "awaiting_merge") return true;
  return OPEN_STATES.has(task.workState);
}

function isClosedTask(task: FunderTask, autoMerge: boolean | null): boolean {
  if (isDecisionTask(task, autoMerge) || isOpenTask(task, autoMerge)) return false;
  return CLOSED_STATES.has(task.workState);
}

function isUnknownTask(task: FunderTask, autoMerge: boolean | null): boolean {
  return (
    !isDecisionTask(task, autoMerge) &&
    !isOpenTask(task, autoMerge) &&
    !isClosedTask(task, autoMerge)
  );
}

export function assessRepo(facts: RepoFacts): RepoAssessment {
  const meta = repoMeta(facts.bountyCount, facts.alertCount);
  const autoMerge = facts.autoMergeAcceptable;
  const tasks = facts.tasksKnown ? facts.tasks : [];
  const decisions = tasks.filter((task) => isDecisionTask(task, autoMerge));
  const repoFindings =
    typeof facts.openFindings === "number" && facts.openFindings > 0 ? Math.trunc(facts.openFindings) : 0;

  if (facts.tasksKnown && decisions.length > 0) {
    return { status: "needs_attention", reason: attentionReason(decisions.length), meta };
  }
  if (repoFindings > 0 && decisions.length === 0) {
    return { status: "needs_attention", reason: attentionReason(repoFindings), meta };
  }

  const open = tasks.filter((task) => isOpenTask(task, autoMerge));
  if (facts.tasksKnown && open.length > 0) {
    return { status: "task_in_progress", reason: "Task in progress", meta };
  }
  if (facts.paused === true) {
    return {
      status: "paused",
      reason: "Funding for this repository is paused.",
      meta,
    };
  }
  if (facts.appInstalled === false || facts.linkEnabled === false) {
    return {
      status: "setup_needed",
      reason:
        facts.appInstalled === false
          ? "The SettleBridge GitHub App is not installed."
          : "The repository link is disabled.",
      meta,
    };
  }

  const unknown = tasks.filter((task) => isUnknownTask(task, autoMerge));
  const assessed =
    facts.tasksKnown &&
    unknown.length === 0 &&
    (facts.appInstalled === true || facts.linkEnabled === true);
  if (!assessed) {
    return { status: "not_assessed", reason: "Repository data was not returned.", meta };
  }
  return { status: "no_open_tasks", reason: "No open tasks", meta };
}

export function sortRepoAssessments<T extends { status: RepoStatus; repo: string }>(
  items: readonly T[]
): T[] {
  return [...items].sort((a, b) => {
    const byStatus = REPO_STATUS_ORDER.indexOf(a.status) - REPO_STATUS_ORDER.indexOf(b.status);
    if (byStatus !== 0) return byStatus;
    return a.repo.localeCompare(b.repo);
  });
}

export function approvalsWaiting<T extends ApprovalTask>(tasks: readonly T[]): T[] {
  return tasks.filter((task) => {
    if (task.workState === "needs_review" || task.workState === "needs_maintainer_review") {
      return true;
    }
    if (task.workState === "awaiting_merge") return task.autoMergeAcceptable === false;
    return false;
  });
}

function sameRepo(left: string | null | undefined, right: string): boolean {
  return typeof left === "string" && left.toLowerCase() === right.toLowerCase();
}

function resolveWorkState(owned: OwnedBounty, cyber: CyberHit | undefined): string {
  if (cyber && cyber.workState.trim()) return cyber.workState.trim();
  if (owned.ledgerState) {
    return ledgerStateToWorkState(owned.ledgerState) ?? "unknown";
  }
  if (owned.bountyStatus) {
    return bountyStatusToWorkState(owned.bountyStatus) ?? "unknown";
  }
  return "unknown";
}

export function collectRepoTasks(input: {
  repo: string;
  ownedKnown: boolean;
  owned: readonly OwnedBounty[];
  cyber: readonly CyberHit[] | null;
  cyberComplete: boolean;
  autoMergeAcceptable: boolean | null;
}): {
  tasksKnown: boolean;
  tasks: FunderTask[];
  alertCount: number | null;
  bountyCount: number | null;
} {
  const ownedIds = new Set(input.owned.map((bounty) => bounty.bountyId));
  const onRepo = input.owned.filter((bounty) => sameRepo(bounty.repo, input.repo));
  const cyberById = new Map<string, CyberHit>();
  if (input.cyber) {
    for (const hit of input.cyber) {
      if (!hit.bountyId || !ownedIds.has(hit.bountyId)) continue;
      const ownedHere = onRepo.some((bounty) => bounty.bountyId === hit.bountyId);
      if (sameRepo(hit.repo, input.repo) || ownedHere) cyberById.set(hit.bountyId, hit);
    }
  }

  const ids = new Set<string>(onRepo.map((bounty) => bounty.bountyId));
  for (const id of cyberById.keys()) ids.add(id);
  const ownedById = new Map(input.owned.map((bounty) => [bounty.bountyId, bounty]));
  const tasks: FunderTask[] = [];
  for (const id of ids) {
    const owned = ownedById.get(id);
    const hit = cyberById.get(id);
    const base: OwnedBounty = owned ?? {
      bountyId: id,
      title: hit?.title ?? "",
      repo: input.repo,
      bountyStatus: null,
      ledgerState: null,
    };
    tasks.push({
      bountyId: id,
      title: (hit?.title || base.title || "").trim(),
      workState: resolveWorkState(base, hit),
      openFindings: hit ? hit.openFindings : null,
      source: hit?.source ?? null,
    });
  }
  tasks.sort((a, b) => a.bountyId.localeCompare(b.bountyId));

  const alertCount =
    input.cyber !== null && input.cyberComplete
      ? tasks.filter((task) => task.source !== null && ALERT_SOURCES.has(task.source)).length
      : null;
  const bountyCount = input.ownedKnown ? ids.size : null;
  const classesKnown = input.ownedKnown || input.cyber !== null;
  const hasActive = tasks.some(
    (task) =>
      isDecisionTask(task, input.autoMergeAcceptable) ||
      isOpenTask(task, input.autoMergeAcceptable)
  );
  let tasksKnown = false;
  if (classesKnown && hasActive) tasksKnown = true;
  else if (classesKnown && (input.cyber === null || input.cyberComplete)) tasksKnown = true;
  return { tasksKnown, tasks, alertCount, bountyCount };
}

export function readSetup(input: { denied: boolean; snapshot: SetupSnapshot | null }): SetupRead {
  if (input.denied) {
    return {
      appInstalled: false,
      linkEnabled: null,
      autoMergeAcceptable: null,
      headroomAte: null,
      minimumAte: null,
    };
  }
  if (!input.snapshot) {
    return {
      appInstalled: null,
      linkEnabled: null,
      autoMergeAcceptable: null,
      headroomAte: null,
      minimumAte: null,
    };
  }
  return {
    appInstalled: input.snapshot.repoAccess,
    linkEnabled: input.snapshot.enabled,
    autoMergeAcceptable: input.snapshot.autoMergeAcceptable,
    headroomAte: finiteOrNull(input.snapshot.headroomAte),
    minimumAte: finiteOrNull(input.snapshot.minimumAte),
  };
}

export function pickBudget(input: {
  setups: readonly { headroomAte: number | null; minimumAte: number | null }[];
  ledgerFreeAte: number | null;
  ledgerExchangeFailed: boolean;
}): BudgetNumbers {
  for (const setup of input.setups) {
    if (typeof setup.headroomAte === "number" && Number.isFinite(setup.headroomAte)) {
      return {
        headroomAte: setup.headroomAte,
        minimumAte: finiteOrNull(setup.minimumAte),
      };
    }
  }
  if (
    !input.ledgerExchangeFailed &&
    typeof input.ledgerFreeAte === "number" &&
    Number.isFinite(input.ledgerFreeAte)
  ) {
    return { headroomAte: input.ledgerFreeAte, minimumAte: null };
  }
  return { headroomAte: null, minimumAte: null };
}

function numberOrNull(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value;
}

export function readLedgerFunder(payload: unknown): {
  rows: FunderLedgerRow[];
  freeAte: number | null;
  exchangeFailed: boolean;
} | null {
  if (!payload || typeof payload !== "object") return null;
  const record = payload as { entries?: unknown; free_ate?: unknown; exchange_error?: unknown };
  if (!Array.isArray(record.entries)) return null;
  const rows: FunderLedgerRow[] = [];
  for (const entry of record.entries) {
    if (!entry || typeof entry !== "object") continue;
    const row = entry as {
      role?: unknown;
      bounty_id?: unknown;
      repo?: unknown;
      issue_number?: unknown;
      title?: unknown;
      state?: unknown;
      amount_ate?: unknown;
      fee_ate?: unknown;
    };
    if (row.role !== "funder") continue;
    if (typeof row.bounty_id !== "string" || typeof row.repo !== "string") continue;
    rows.push({
      bountyId: row.bounty_id,
      repo: row.repo,
      issueNumber: typeof row.issue_number === "number" && Number.isFinite(row.issue_number) ? row.issue_number : 0,
      title: typeof row.title === "string" ? row.title : "",
      state: typeof row.state === "string" ? row.state : "",
      amountAte: numberOrNull(row.amount_ate),
      feeAte: numberOrNull(row.fee_ate),
    });
  }
  const exchangeFailed = typeof record.exchange_error === "string" && record.exchange_error.trim().length > 0;
  return {
    rows,
    freeAte: numberOrNull(record.free_ate),
    exchangeFailed,
  };
}

export function mergeOwned(input: {
  ledgerRows: readonly FunderLedgerRow[] | null;
  posted: readonly { id: string; title: string; status: string; tags: readonly string[] | null }[] | null;
}): { ownedKnown: boolean; owned: OwnedBounty[] } {
  if (input.ledgerRows === null && input.posted === null) {
    return { ownedKnown: false, owned: [] };
  }
  const byId = new Map<string, OwnedBounty>();
  for (const row of input.ledgerRows ?? []) {
    if (!row.bountyId) continue;
    byId.set(row.bountyId, {
      bountyId: row.bountyId,
      title: row.title,
      repo: isRepoName(row.repo) ? row.repo : null,
      bountyStatus: null,
      ledgerState: row.state || null,
    });
  }
  for (const bounty of input.posted ?? []) {
    if (!bounty.id) continue;
    const repo = repoFromTags(bounty.tags);
    const existing = byId.get(bounty.id);
    if (existing) {
      byId.set(bounty.id, {
        ...existing,
        title: existing.title || bounty.title,
        repo: existing.repo ?? repo,
        bountyStatus: bounty.status,
      });
    } else if (repo) {
      byId.set(bounty.id, {
        bountyId: bounty.id,
        title: bounty.title,
        repo,
        bountyStatus: bounty.status,
        ledgerState: null,
      });
    }
  }
  return { ownedKnown: true, owned: [...byId.values()] };
}

function dedupeApprovals(tasks: readonly ApprovalTask[]): ApprovalTask[] {
  const seen = new Set<string>();
  const approvals: ApprovalTask[] = [];
  for (const task of tasks) {
    if (!task.bountyId || seen.has(task.bountyId)) continue;
    seen.add(task.bountyId);
    approvals.push(task);
  }
  approvals.sort(
    (a, b) =>
      a.repo.localeCompare(b.repo) ||
      a.title.localeCompare(b.title) ||
      a.bountyId.localeCompare(b.bountyId)
  );
  return approvals;
}

export function buildFunderModel(input: FunderModelInput): FunderModel {
  const ownedIds = new Set(input.owned.map((bounty) => bounty.bountyId));
  const cyberRepos =
    input.cyber === null
      ? []
      : input.cyber.filter((hit) => ownedIds.has(hit.bountyId)).map((hit) => hit.repo);
  const linked = input.linked === undefined ? [] : input.linked;
  const repoNames = uniqueRepos([
    ...(linked ?? []),
    ...input.owned.map((bounty) => bounty.repo),
    ...cyberRepos,
    input.contextRepo,
  ]);
  const setupByKey = new Map<string, (typeof input.setups)[number]>();
  for (const setup of input.setups) setupByKey.set(setup.repo.toLowerCase(), setup);

  const cards: FunderRepoCard[] = [];
  const approvalInputs: ApprovalTask[] = [];
  const budgetSources: { headroomAte: number | null; minimumAte: number | null }[] = [];

  for (const repo of repoNames) {
    const setup = setupByKey.get(repo.toLowerCase());
    const read = readSetup({
      denied: setup?.denied ?? false,
      snapshot: setup?.snapshot ?? null,
    });
    const collected = collectRepoTasks({
      repo,
      ownedKnown: input.ownedKnown,
      owned: input.owned,
      cyber: input.cyber,
      cyberComplete: input.cyberComplete,
      autoMergeAcceptable: read.autoMergeAcceptable,
    });
    const assessment = assessRepo({
      repo,
      appInstalled: read.appInstalled,
      linkEnabled: read.linkEnabled,
      paused: null,
      tasksKnown: collected.tasksKnown,
      tasks: collected.tasks,
      openFindings: null,
      bountyCount: collected.bountyCount,
      alertCount: collected.alertCount,
      autoMergeAcceptable: read.autoMergeAcceptable,
    });
    cards.push({
      repo,
      status: assessment.status,
      reason: assessment.reason,
      meta: assessment.meta,
    });
    budgetSources.push({ headroomAte: read.headroomAte, minimumAte: read.minimumAte });
    for (const task of collected.tasks) {
      approvalInputs.push({
        bountyId: task.bountyId,
        title: task.title,
        repo,
        workState: task.workState,
        autoMergeAcceptable: read.autoMergeAcceptable,
      });
    }
  }

  return {
    repos: sortRepoAssessments(cards),
    approvals: dedupeApprovals(approvalsWaiting(approvalInputs)),
    budget: pickBudget({
      setups: budgetSources,
      ledgerFreeAte: input.ledgerFreeAte,
      ledgerExchangeFailed: input.ledgerExchangeFailed,
    }),
    tasksIncomplete: input.cyber !== null && !input.cyberComplete,
    discoveryComplete: input.ownedKnown && linked !== null,
  };
}
