// Pure solver-home rules. `needs_review` means the maintainer must review; the
// solver owes nothing then. An open work window comes first: the solver owes a
// pull request before work_expires_at.

import type { RepoStatus } from "@/lib/cyberContracts";

export interface SolverLeaseInput {
  lease_id: string;
  bounty_id: string;
  repo: string;
  issue_number: number;
  status: string;
  work_expires_at: string;
  pr_number: number | null;
  evaluation: "acceptable" | "needs_review" | null;
  payout: "paid" | "pending" | "not_paid" | null;
  amount_ate: number;
}

export interface EligibleSolverBounty {
  bounty_id: string;
  repo: string;
  issue_number: number;
  title: string;
  amount_ate: number;
  required_ema: number;
}

export interface AvailableCyberTaskInput {
  bounty_id: string;
  title?: string;
  repo?: string;
  issue_number?: number | null;
  reward_ate?: number | null;
  work_state?: string;
  eligibility?: {
    min_reputation?: number | null;
    min_kya?: number | null;
  } | null;
}

export interface SolverRepoModel {
  repo: string;
  status: RepoStatus;
  reason: string;
  bountyId: string | null;
  meta?: string;
}

export interface SolverAvailableTask {
  bountyId: string;
  title: string;
  repo: string;
  issueNumber: number | null;
  amountAte: number | null;
  requiredReputation: number | null;
  requiredKya: number | null;
}

export interface EarningsSummary {
  paidAte: number;
  pendingAte: number;
}

const KNOWN_LEASE_STATUSES = new Set(["work", "submitted", "released", "expired", "settled"]);

const SIGNAL_RANK = {
  needs_attention: 0,
  task_in_progress: 1,
  not_assessed: 2,
} as const;

type ActiveRepoStatus = keyof typeof SIGNAL_RANK;

interface LeaseSignal {
  status: ActiveRepoStatus;
  reason: string;
  deadline: number | null;
}

interface RepoBucket {
  repo: string;
  leases: SolverLeaseInput[];
  eligibleCount: number;
  soleEligibleId: string | null;
}

const REPO_NAME = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

const deadlineFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "UTC",
  timeZoneName: "short",
});

export function formatUtcTimestamp(iso: string): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso.trim();
  return deadlineFormat.format(new Date(ms));
}

export function formatAte(amount: number): string {
  if (typeof amount !== "number" || !Number.isFinite(amount)) return "0 ATE (test)";
  return `${amount} ATE (test)`;
}

export function formatThreshold(value: number): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  return String(Math.round(value * 1000) / 1000);
}

export function payoutFundingState(
  payout: SolverLeaseInput["payout"]
): "paid" | "payment_pending" | "not_paid" | null {
  if (payout === "paid") return "paid";
  if (payout === "pending") return "payment_pending";
  if (payout === "not_paid") return "not_paid";
  return null;
}

export function leaseStatusLabel(status: string): string {
  switch (status) {
    case "work":
      return "Claimed";
    case "submitted":
      return "Submitted";
    case "released":
      return "Released";
    case "expired":
      return "Expired";
    case "settled":
      return "Settled";
    default:
      return "Not assessed";
  }
}

export function pullRequestUrl(repo: string, prNumber: number | null): string | null {
  if (prNumber == null || !Number.isInteger(prNumber) || prNumber <= 0) return null;
  const name = repo.trim();
  if (!REPO_NAME.test(name)) return null;
  return `https://github.com/${name}/pull/${prNumber}`;
}

export function summarizeEarnings(
  leases: readonly Pick<SolverLeaseInput, "payout" | "amount_ate">[]
): EarningsSummary {
  let paidAte = 0;
  let pendingAte = 0;
  for (const lease of leases) {
    if (typeof lease.amount_ate !== "number" || !Number.isFinite(lease.amount_ate)) continue;
    if (lease.payout === "paid") paidAte += lease.amount_ate;
    else if (lease.payout === "pending") pendingAte += lease.amount_ate;
  }
  return { paidAte, pendingAte };
}

export function dedupeAvailableTasks(
  eligible: readonly EligibleSolverBounty[],
  cyberTasks: readonly AvailableCyberTaskInput[]
): SolverAvailableTask[] {
  const cyberById = new Map<string, AvailableCyberTaskInput>();
  for (const task of cyberTasks) {
    if (task.work_state !== "available") continue;
    const id = cleanId(task.bounty_id);
    if (!id || cyberById.has(id)) continue;
    cyberById.set(id, task);
  }

  const seen = new Set<string>();
  const tasks: SolverAvailableTask[] = [];

  for (const bounty of eligible) {
    const id = cleanId(bounty.bounty_id);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    tasks.push(fromEligible(bounty, cyberById.get(id)));
  }

  for (const [id, task] of cyberById) {
    if (seen.has(id)) continue;
    tasks.push(fromCyber(id, task));
  }

  return tasks;
}

export function deriveSolverRepos(
  leases: readonly SolverLeaseInput[],
  eligible: readonly Pick<EligibleSolverBounty, "bounty_id" | "repo">[],
  now: number
): SolverRepoModel[] {
  const buckets = new Map<string, RepoBucket>();

  const bucketFor = (repo: string): RepoBucket | null => {
    const trimmed = repo.trim();
    if (!trimmed) return null;
    const key = trimmed.toLowerCase();
    const existing = buckets.get(key);
    if (existing) return existing;
    const created: RepoBucket = {
      repo: trimmed,
      leases: [],
      eligibleCount: 0,
      soleEligibleId: null,
    };
    buckets.set(key, created);
    return created;
  };

  for (const lease of leases) {
    const bucket = bucketFor(lease.repo);
    if (bucket) bucket.leases.push(lease);
  }

  for (const task of eligible) {
    const bucket = bucketFor(task.repo);
    if (!bucket) continue;
    bucket.eligibleCount += 1;
    const id = cleanId(task.bounty_id);
    if (!id) continue;
    if (bucket.soleEligibleId == null) bucket.soleEligibleId = id;
    else if (bucket.soleEligibleId !== id) bucket.soleEligibleId = "";
  }

  return [...buckets.values()].map((bucket) => summarizeRepo(bucket, now));
}

function summarizeRepo(bucket: RepoBucket, now: number): SolverRepoModel {
  let best: { signal: LeaseSignal; bountyId: string } | null = null;

  for (const lease of bucket.leases) {
    const signal = classifyLease(lease, now);
    if (!signal) continue;
    if (
      best == null ||
      SIGNAL_RANK[signal.status] < SIGNAL_RANK[best.signal.status] ||
      (SIGNAL_RANK[signal.status] === SIGNAL_RANK[best.signal.status] &&
        (signal.deadline ?? Number.POSITIVE_INFINITY) <
          (best.signal.deadline ?? Number.POSITIVE_INFINITY))
    ) {
      best = { signal, bountyId: cleanId(lease.bounty_id) };
    }
  }

  if (best) {
    return {
      repo: bucket.repo,
      status: best.signal.status,
      reason: best.signal.reason,
      bountyId: best.bountyId || null,
    };
  }

  const soleEligible = bucket.eligibleCount === 1 ? bucket.soleEligibleId || null : null;
  return {
    repo: bucket.repo,
    status: "no_open_tasks",
    reason: "No active work",
    bountyId: soleEligible,
    meta: eligibleMeta(bucket.eligibleCount),
  };
}

function eligibleMeta(count: number): string | undefined {
  if (count === 1) return "Eligible to claim";
  if (count > 1) return `${count} eligible tasks`;
  return undefined;
}

function classifyLease(lease: SolverLeaseInput, now: number): LeaseSignal | null {
  if (!KNOWN_LEASE_STATUSES.has(lease.status)) {
    return { status: "not_assessed", reason: "Not assessed", deadline: null };
  }

  if (lease.status === "work") {
    const expires = expiryMs(lease.work_expires_at);
    if (expires == null) {
      return { status: "not_assessed", reason: "Not assessed", deadline: null };
    }
    if (expires >= now) {
      return {
        status: "needs_attention",
        reason: `Open a pull request before ${formatUtcTimestamp(lease.work_expires_at)}.`,
        deadline: expires,
      };
    }
  }

  if (lease.status === "submitted") {
    return {
      status: "task_in_progress",
      reason:
        lease.evaluation === "needs_review"
          ? "Waiting for maintainer review"
          : lease.evaluation === "acceptable"
            ? "Pull request submitted"
            : "Checks are running",
      deadline: null,
    };
  }

  return null;
}

function fromEligible(
  bounty: EligibleSolverBounty,
  cyber: AvailableCyberTaskInput | undefined
): SolverAvailableTask {
  const repo = text(bounty.repo) || text(cyber?.repo);
  const issueNumber = finiteNumber(bounty.issue_number) ?? finiteNumber(cyber?.issue_number);
  const title = text(bounty.title) || text(cyber?.title);
  return {
    bountyId: cleanId(bounty.bounty_id),
    title: taskTitle(title, repo, issueNumber),
    repo,
    issueNumber,
    amountAte: finiteNumber(bounty.amount_ate) ?? finiteNumber(cyber?.reward_ate),
    requiredReputation:
      finiteNumber(bounty.required_ema) ?? finiteNumber(cyber?.eligibility?.min_reputation),
    requiredKya: finiteNumber(cyber?.eligibility?.min_kya),
  };
}

function fromCyber(id: string, task: AvailableCyberTaskInput): SolverAvailableTask {
  const repo = text(task.repo);
  const issueNumber = finiteNumber(task.issue_number);
  return {
    bountyId: id,
    title: taskTitle(text(task.title), repo, issueNumber),
    repo,
    issueNumber,
    amountAte: finiteNumber(task.reward_ate),
    requiredReputation: finiteNumber(task.eligibility?.min_reputation),
    requiredKya: finiteNumber(task.eligibility?.min_kya),
  };
}

function taskTitle(title: string, repo: string, issueNumber: number | null): string {
  if (title) return title;
  if (repo && issueNumber != null) return `${repo}#${issueNumber}`;
  if (repo) return repo;
  return "Task";
}

function cleanId(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function expiryMs(iso: string): number | null {
  if (typeof iso !== "string" || !iso.trim()) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : ms;
}
