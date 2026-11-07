"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import axios from "axios";
import { AlertCircle, Coins } from "lucide-react";
import RepoCard, { sortRepoCards } from "@/components/RepoCard";
import { FundingBadge, StatusBadge } from "@/components/StatusBadge";
import MyClaims from "@/components/marketplace-pages/MyClaims";
import { NotSetUpPanel } from "@/components/NotSetUpPanel";
import { useWorkContext } from "@/hooks/useWorkContext";
import type { CyberTask } from "@/lib/types";
import api, { fetchCyberTasks } from "@/services/api";
import { withContext } from "@/lib/workContext";
import {
  dedupeAvailableTasks,
  deriveSolverRepos,
  formatAte,
  formatThreshold,
  formatUtcTimestamp,
  leaseStatusLabel,
  payoutFundingState,
  pullRequestUrl,
  summarizeEarnings,
  type EligibleSolverBounty,
  type SolverAvailableTask,
  type SolverLeaseInput,
} from "@/lib/solverView";

const CYBER_PAGE_SIZE = 50;

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-money rounded-sm";

type Load<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T; asOf: number };

interface SetupStatus {
  ready: boolean;
  eligible_bounties: EligibleSolverBounty[];
}

interface CyberPage {
  items: CyberTask[];
  total: number;
}

const PAYOUTS = new Set(["paid", "pending", "not_paid"]);
const EVALUATIONS = new Set(["acceptable", "needs_review"]);

function messageFrom(err: unknown, fallback: string): string {
  if (!axios.isAxiosError(err)) return fallback;
  const detail = err.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (
    detail &&
    typeof detail === "object" &&
    "message" in detail &&
    typeof detail.message === "string" &&
    detail.message.trim()
  ) {
    return detail.message;
  }
  return fallback;
}

function textOrNull(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function readEligible(value: unknown): EligibleSolverBounty[] | null {
  if (!Array.isArray(value)) return null;
  const bounties: EligibleSolverBounty[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object") return null;
    const row = item as Record<string, unknown>;
    if (typeof row.bounty_id !== "string" || typeof row.repo !== "string") return null;
    if (typeof row.issue_number !== "number" || typeof row.amount_ate !== "number") return null;
    if (typeof row.required_ema !== "number") return null;
    bounties.push({
      bounty_id: row.bounty_id,
      repo: row.repo,
      issue_number: row.issue_number,
      title: typeof row.title === "string" ? row.title : "",
      amount_ate: row.amount_ate,
      required_ema: row.required_ema,
    });
  }
  return bounties;
}

function readSetup(data: unknown): SetupStatus | null {
  if (!data || typeof data !== "object") return null;
  const body = data as Record<string, unknown>;
  if (typeof body.ready !== "boolean") return null;
  const eligible = readEligible(body.eligible_bounties);
  if (!eligible) return null;
  return { ready: body.ready, eligible_bounties: eligible };
}

function readPayout(value: unknown): SolverLeaseInput["payout"] | undefined {
  if (value == null) return null;
  if (typeof value === "string" && PAYOUTS.has(value)) return value as SolverLeaseInput["payout"];
  return undefined;
}

function readEvaluation(value: unknown): SolverLeaseInput["evaluation"] | undefined {
  if (value == null) return null;
  if (typeof value === "string" && EVALUATIONS.has(value)) {
    return value as SolverLeaseInput["evaluation"];
  }
  return undefined;
}

function readLease(value: unknown): SolverLeaseInput | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  const payout = readPayout(row.payout);
  const evaluation = readEvaluation(row.evaluation);
  if (payout === undefined || evaluation === undefined) return null;
  if (typeof row.lease_id !== "string" || typeof row.bounty_id !== "string") return null;
  if (typeof row.repo !== "string" || typeof row.status !== "string") return null;
  if (typeof row.issue_number !== "number" || typeof row.amount_ate !== "number") return null;
  if (typeof row.work_expires_at !== "string") return null;
  if (row.pr_number != null && typeof row.pr_number !== "number") return null;
  return {
    lease_id: row.lease_id,
    bounty_id: row.bounty_id,
    repo: row.repo,
    issue_number: row.issue_number,
    status: row.status,
    work_expires_at: row.work_expires_at,
    pr_number: typeof row.pr_number === "number" ? row.pr_number : null,
    evaluation,
    payout,
    amount_ate: row.amount_ate,
  };
}

function readLeases(data: unknown): SolverLeaseInput[] | null {
  if (!data || typeof data !== "object") return null;
  const items = (data as { items?: unknown }).items;
  if (!Array.isArray(items)) return null;
  const leases: SolverLeaseInput[] = [];
  for (const item of items) {
    const lease = readLease(item);
    if (!lease) return null;
    leases.push(lease);
  }
  return leases;
}

function LoadingBlock({ label }: { label: string }) {
  return (
    <div aria-busy="true">
      <p className="sr-only">{label}</p>
      <div className="space-y-3">
        {[1, 2, 3].map((item) => (
          <div
            key={item}
            className="h-20 animate-pulse rounded-xl border border-gray-200 bg-white"
          />
        ))}
      </div>
    </div>
  );
}

function SectionError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">
      <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
      <span className="min-w-0 flex-1">{message}</span>
      <button
        type="button"
        onClick={onRetry}
        className={`rounded-lg border border-red-200 bg-white px-3 py-1.5 text-xs font-medium text-red-800 hover:bg-red-50 ${focusRing}`}
      >
        Retry
      </button>
    </div>
  );
}

function EmptyCopy({ children }: { children: string }) {
  return <p className="text-sm text-gray-500">{children}</p>;
}

function EvaluationLine({ evaluation }: { evaluation: SolverLeaseInput["evaluation"] }) {
  if (evaluation === "acceptable") {
    return <StatusBadge state="in_progress" label="Acceptable" />;
  }
  if (evaluation === "needs_review") {
    return <StatusBadge state="in_progress" label="Waiting for maintainer review" />;
  }
  return <StatusBadge state="not_assessed" label="Not assessed" />;
}

function PayoutLine({ payout }: { payout: SolverLeaseInput["payout"] }) {
  const state = payoutFundingState(payout);
  if (!state) return <StatusBadge state="not_assessed" label="Not assessed" />;
  return <FundingBadge state={state} />;
}

function observedAt(setup: Load<SetupStatus>, leases: Load<SolverLeaseInput[]>): number {
  const times: number[] = [];
  if (setup.status === "ready") times.push(setup.asOf);
  if (leases.status === "ready") times.push(leases.asOf);
  return times.length === 0 ? 0 : Math.max(...times);
}

function workWindowText(iso: string): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "Work window: Not assessed";
  return `Work window ends ${formatUtcTimestamp(iso)}`;
}

export default function SolverHome() {
  const { context } = useWorkContext();
  const [setupAttempt, setSetupAttempt] = useState(0);
  const [leaseAttempt, setLeaseAttempt] = useState(0);
  const [cyberAttempt, setCyberAttempt] = useState(0);
  const [setup, setSetup] = useState<Load<SetupStatus>>({ status: "loading" });
  const [leases, setLeases] = useState<Load<SolverLeaseInput[]>>({ status: "loading" });
  const [cyber, setCyber] = useState<Load<CyberPage>>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setSetup({ status: "loading" });
    api
      .get<unknown>("/v1/github/solver/setup-status")
      .then(({ data }) => {
        if (cancelled) return;
        const parsed = readSetup(data);
        if (!parsed) {
          setSetup({ status: "error", message: "Could not load solver setup." });
          return;
        }
        setSetup({ status: "ready", data: parsed, asOf: Date.now() });
      })
      .catch((err: unknown) => {
        if (!cancelled) setSetup({ status: "error", message: messageFrom(err, "Could not load solver setup.") });
      });
    return () => {
      cancelled = true;
    };
  }, [setupAttempt]);

  useEffect(() => {
    let cancelled = false;
    setLeases({ status: "loading" });
    api
      .get<unknown>("/v1/github/leases/mine")
      .then(({ data }) => {
        if (cancelled) return;
        const parsed = readLeases(data);
        if (!parsed) {
          setLeases({ status: "error", message: "Could not load your leases." });
          return;
        }
        setLeases({ status: "ready", data: parsed, asOf: Date.now() });
      })
      .catch((err: unknown) => {
        if (!cancelled) setLeases({ status: "error", message: messageFrom(err, "Could not load your leases.") });
      });
    return () => {
      cancelled = true;
    };
  }, [leaseAttempt]);

  useEffect(() => {
    let cancelled = false;
    setCyber({ status: "loading" });
    fetchCyberTasks({ view: "available", page: 1, page_size: CYBER_PAGE_SIZE })
      .then((data) => {
        if (cancelled) return;
        const items = Array.isArray(data?.items) ? data.items : [];
        const total =
          data && typeof data.total === "number" && Number.isFinite(data.total) ? data.total : items.length;
        setCyber({ status: "ready", data: { items, total }, asOf: Date.now() });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setCyber({ status: "error", message: messageFrom(err, "Could not load available tasks.") });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [cyberAttempt]);

  const notSetUp =
    setup.status === "ready" &&
    setup.data.ready === false &&
    setup.data.eligible_bounties.length === 0 &&
    leases.status === "ready" &&
    leases.data.length === 0;

  if (notSetUp) {
    return (
      <div className="space-y-10">
        <NotSetUpPanel view="solve" href={withContext("/dashboard/github/solver", context)} />
        <MyClaims />
      </div>
    );
  }

  return (
    <div className="space-y-10">
      <ReposSection
        setup={setup}
        leases={leases}
        contextHref={(href, repo) =>
          repo ? withContext(href, { ...context, repo, task: null }) : withContext(href, context)
        }
        onRetrySetup={() => setSetupAttempt((attempt) => attempt + 1)}
        onRetryLeases={() => setLeaseAttempt((attempt) => attempt + 1)}
      />
      <AvailableSection
        setup={setup}
        cyber={cyber}
        taskHref={(bountyId) => withContext(`/bounties/${encodeURIComponent(bountyId)}`, context)}
        moreHref={withContext("/cyber", context)}
        onRetrySetup={() => setSetupAttempt((attempt) => attempt + 1)}
        onRetryCyber={() => setCyberAttempt((attempt) => attempt + 1)}
      />
      <LeasesSection
        leases={leases}
        taskHref={(bountyId) => withContext(`/bounties/${encodeURIComponent(bountyId)}`, context)}
        onRetry={() => setLeaseAttempt((attempt) => attempt + 1)}
      />
      <EarningsSection leases={leases} onRetry={() => setLeaseAttempt((attempt) => attempt + 1)} />
    </div>
  );
}

function ReposSection({
  setup,
  leases,
  contextHref,
  onRetrySetup,
  onRetryLeases,
}: {
  setup: Load<SetupStatus>;
  leases: Load<SolverLeaseInput[]>;
  contextHref: (href: string, repo: string | null) => string;
  onRetrySetup: () => void;
  onRetryLeases: () => void;
}) {
  const settled = setup.status !== "loading" && leases.status !== "loading";
  const bothReady = setup.status === "ready" && leases.status === "ready";
  const leaseRows = leases.status === "ready" ? leases.data : [];
  const eligible = setup.status === "ready" ? setup.data.eligible_bounties : [];
  const asOf = observedAt(setup, leases);
  const models = settled ? deriveSolverRepos(leaseRows, eligible, asOf) : [];
  const cards = sortRepoCards(
    models.map((model) => ({
      repo: model.repo,
      status: model.status,
      reason: model.reason,
      meta: model.meta,
      href: model.bountyId
        ? contextHref(`/bounties/${encodeURIComponent(model.bountyId)}`, null)
        : contextHref("/bounties", model.repo),
    }))
  );

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-navy-900">Repos I solve for</h2>
      {setup.status === "error" ? <SectionError message={setup.message} onRetry={onRetrySetup} /> : null}
      {leases.status === "error" ? <SectionError message={leases.message} onRetry={onRetryLeases} /> : null}
      {!settled ? (
        <LoadingBlock label="Loading repositories" />
      ) : cards.length === 0 ? (
        bothReady ? <EmptyCopy>No repositories yet.</EmptyCopy> : null
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {cards.map((card) => (
            <RepoCard key={card.repo} {...card} />
          ))}
        </div>
      )}
    </section>
  );
}

function AvailableSection({
  setup,
  cyber,
  taskHref,
  moreHref,
  onRetrySetup,
  onRetryCyber,
}: {
  setup: Load<SetupStatus>;
  cyber: Load<CyberPage>;
  taskHref: (bountyId: string) => string;
  moreHref: string;
  onRetrySetup: () => void;
  onRetryCyber: () => void;
}) {
  const settled = setup.status !== "loading" && cyber.status !== "loading";
  const bothReady = setup.status === "ready" && cyber.status === "ready";
  const tasks = settled
    ? dedupeAvailableTasks(
        setup.status === "ready" ? setup.data.eligible_bounties : [],
        cyber.status === "ready" ? cyber.data.items : []
      )
    : [];
  const truncated =
    cyber.status === "ready" && cyber.data.total > cyber.data.items.length;

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-navy-900">Available tasks</h2>
      {setup.status === "error" ? <SectionError message={setup.message} onRetry={onRetrySetup} /> : null}
      {cyber.status === "error" ? <SectionError message={cyber.message} onRetry={onRetryCyber} /> : null}
      {!settled ? (
        <LoadingBlock label="Loading available tasks" />
      ) : tasks.length === 0 ? (
        bothReady ? <EmptyCopy>No tasks you can claim right now.</EmptyCopy> : null
      ) : (
        <ul className="space-y-2">
          {tasks.map((task) => (
            <li key={task.bountyId}>
              <AvailableTask task={task} href={taskHref(task.bountyId)} />
            </li>
          ))}
        </ul>
      )}
      {truncated ? (
        <p className="text-sm text-gray-500">
          More tasks are on the{" "}
          <Link href={moreHref} className={`font-medium text-navy-700 hover:underline ${focusRing}`}>
            Cyber board
          </Link>
          .
        </p>
      ) : null}
    </section>
  );
}

function AvailableTask({ task, href }: { task: SolverAvailableTask; href: string }) {
  const reputation =
    task.requiredReputation == null ? "" : formatThreshold(task.requiredReputation);
  const kya = task.requiredKya == null ? "" : formatThreshold(task.requiredKya);
  const issue =
    task.repo && task.issueNumber != null
      ? `${task.repo}#${task.issueNumber}`
      : task.repo || "";

  return (
    <Link href={href} className={`block rounded-lg border border-gray-200 bg-white p-3 hover:bg-gray-50 ${focusRing}`}>
      <span className="block font-medium text-navy-900">{task.title}</span>
      {issue ? <span className="mt-0.5 block font-mono text-xs text-gray-500">{issue}</span> : null}
      <span className="mt-2 flex items-center gap-1.5 text-sm font-semibold text-navy-900">
        <Coins className="h-4 w-4 text-navy-700" aria-hidden="true" />
        {task.amountAte == null ? "ATE (test)" : formatAte(task.amountAte)}
      </span>
      {reputation ? <span className="mt-1 block text-sm text-navy-900">Needs reputation {reputation}</span> : null}
      {kya ? <span className="mt-1 block text-sm text-navy-900">Needs KYA {kya}</span> : null}
    </Link>
  );
}

function LeasesSection({
  leases,
  taskHref,
  onRetry,
}: {
  leases: Load<SolverLeaseInput[]>;
  taskHref: (bountyId: string) => string;
  onRetry: () => void;
}) {
  return (
    <section className="space-y-6">
      <div className="space-y-3">
        <h2 className="text-lg font-semibold text-navy-900">My leases and submissions</h2>
        {leases.status === "error" ? <SectionError message={leases.message} onRetry={onRetry} /> : null}
        {leases.status === "loading" ? (
          <LoadingBlock label="Loading leases" />
        ) : leases.status === "ready" && leases.data.length === 0 ? (
          <EmptyCopy>No leases yet.</EmptyCopy>
        ) : leases.status === "ready" ? (
          <ul className="space-y-3">
            {leases.data.map((lease) => (
              <li key={lease.lease_id}>
                <LeaseRow lease={lease} href={taskHref(lease.bounty_id)} />
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <MyClaims />
    </section>
  );
}

function LeaseRow({ lease, href }: { lease: SolverLeaseInput; href: string }) {
  const prUrl = pullRequestUrl(lease.repo, lease.pr_number);
  const issue =
    textOrNull(lease.repo) && Number.isFinite(lease.issue_number)
      ? `${lease.repo.trim()}#${lease.issue_number}`
      : "Lease";

  return (
    <article className="space-y-2 rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <Link href={href} className={`text-sm font-medium text-navy-900 hover:underline ${focusRing}`}>
            {issue}
          </Link>
          <p className="mt-0.5 text-xs text-gray-500">{leaseStatusLabel(lease.status)}</p>
        </div>
        <p className="text-sm font-semibold text-navy-900">{formatAte(lease.amount_ate)}</p>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {prUrl ? (
          <a
            href={prUrl}
            target="_blank"
            rel="noopener noreferrer"
            className={`text-sm text-navy-700 hover:underline ${focusRing}`}
          >
            Pull request #{lease.pr_number}
          </a>
        ) : (
          <span className="text-sm text-gray-500">No pull request</span>
        )}
        <span className="inline-flex items-center gap-1.5 text-xs text-gray-500">
          Payment <PayoutLine payout={lease.payout} />
        </span>
        <span className="inline-flex items-center gap-1.5 text-xs text-gray-500">
          Evaluation <EvaluationLine evaluation={lease.evaluation} />
        </span>
      </div>
      <p className="text-xs text-gray-500">{workWindowText(lease.work_expires_at)}</p>
    </article>
  );
}

function EarningsSection({
  leases,
  onRetry,
}: {
  leases: Load<SolverLeaseInput[]>;
  onRetry: () => void;
}) {
  const summary = leases.status === "ready" ? summarizeEarnings(leases.data) : null;

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-navy-900">Earnings</h2>
      {leases.status === "error" ? <SectionError message={leases.message} onRetry={onRetry} /> : null}
      {leases.status === "loading" ? (
        <LoadingBlock label="Loading earnings" />
      ) : summary ? (
        <div className="space-y-3 rounded-xl border border-gray-200 bg-white p-4">
          <p className="text-2xl font-bold text-navy-900">{formatAte(summary.paidAte)}</p>
          {summary.paidAte > 0 ? <FundingBadge state="paid" /> : null}
          <div className="flex flex-wrap items-center gap-2 text-sm text-navy-900">
            <FundingBadge state="payment_pending" />
            <span>{formatAte(summary.pendingAte)}</span>
          </div>
        </div>
      ) : null}
    </section>
  );
}
