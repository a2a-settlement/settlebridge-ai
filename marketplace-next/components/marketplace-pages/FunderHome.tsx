"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import axios from "axios";
import { AlertCircle, FileText, TrendingUp } from "lucide-react";
import BountyCard from "@/components/BountyCard";
import RepoCard, { sortRepoCards } from "@/components/RepoCard";
import { StatusBadge } from "@/components/StatusBadge";
import { useWorkContext, withContext } from "@/hooks/useWorkContext";
import type { WorkContext } from "@/lib/cyberContracts";
import {
  buildFunderModel,
  formatAteTest,
  isRepoName,
  mergeOwned,
  readLedgerFunder,
  readLinkedRepos,
  readOpenFindings,
  setupStatusPath,
  uniqueRepos,
  type ApprovalTask,
  type BudgetNumbers,
  type CyberHit,
  type FunderLedgerRow,
  type FunderModel,
  type SetupSnapshot,
} from "@/lib/funderView";
import type { Bounty, CyberTask, CyberTaskView } from "@/lib/types";
import api, { fetchCyberTasks } from "@/services/api";

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-money";

const DUE_STATUSES = new Set(["claimed", "in_review", "submitted"]);

interface AsyncList<T> {
  loading: boolean;
  error: string;
  items: T[];
}

interface EfficacyItem {
  id: string;
  bounty_id: string;
  score: number | null;
  release_percent: number | null;
  efficacy_check_at: string | null;
  efficacy_criteria: string | null;
  bounty?: Bounty;
}

interface Picture {
  loading: boolean;
  reposError: string;
  approvalsError: string;
  model: FunderModel | null;
}

const emptyList = { loading: true, error: "", items: [] };

function messageFrom(err: unknown, fallback: string): string {
  if (!axios.isAxiosError(err)) return fallback;
  const detail = err.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object" && "message" in detail) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return message;
  }
  return fallback;
}

function statusOf(err: unknown): number | undefined {
  return axios.isAxiosError(err) ? err.response?.status : undefined;
}

function toCyberHit(item: CyberTask): CyberHit | null {
  if (!item || typeof item.bounty_id !== "string" || !item.bounty_id) return null;
  return {
    bountyId: item.bounty_id,
    title: typeof item.title === "string" ? item.title : "",
    repo: typeof item.repo === "string" ? item.repo : "",
    workState: typeof item.work_state === "string" ? item.work_state : "",
    source: typeof item.source === "string" ? item.source : null,
    openFindings: readOpenFindings(item),
  };
}

async function loadCyberView(view: CyberTaskView): Promise<{ tasks: CyberHit[]; complete: boolean }> {
  const pageSize = 50;
  const tasks: CyberHit[] = [];
  let seen = 0;
  let total = 0;
  for (let page = 1; page <= 10; page += 1) {
    const data = await fetchCyberTasks({ view, page, page_size: pageSize });
    const items = Array.isArray(data.items) ? data.items : [];
    total =
      typeof data.total === "number" && Number.isFinite(data.total) && data.total >= 0
        ? data.total
        : items.length;
    for (const item of items) {
      const hit = toCyberHit(item);
      if (hit) tasks.push(hit);
    }
    seen += items.length;
    if (items.length === 0 || items.length < pageSize || seen >= total) {
      return { tasks, complete: seen >= total || items.length < pageSize };
    }
  }
  return { tasks, complete: seen >= total };
}

async function loadCyber(): Promise<{ tasks: CyberHit[]; complete: boolean }> {
  const available = await loadCyberView("available");
  const inProgress = await loadCyberView("in_progress");
  const byId = new Map<string, CyberHit>();
  for (const task of available.tasks) byId.set(task.bountyId, task);
  for (const task of inProgress.tasks) byId.set(task.bountyId, task);
  return {
    tasks: [...byId.values()],
    complete: available.complete && inProgress.complete,
  };
}

function toSnapshot(data: unknown): SetupSnapshot | null {
  if (!data || typeof data !== "object") return null;
  const body = data as {
    repo_access?: unknown;
    enabled?: unknown;
    auto_merge_acceptable?: unknown;
    funder?: { headroom_ate?: unknown; minimum_ate?: unknown } | null;
  };
  const funder = body.funder && typeof body.funder === "object" ? body.funder : null;
  return {
    repoAccess: typeof body.repo_access === "boolean" ? body.repo_access : null,
    enabled: typeof body.enabled === "boolean" ? body.enabled : null,
    autoMergeAcceptable:
      typeof body.auto_merge_acceptable === "boolean" ? body.auto_merge_acceptable : null,
    headroomAte: typeof funder?.headroom_ate === "number" ? funder.headroom_ate : null,
    minimumAte: typeof funder?.minimum_ate === "number" ? funder.minimum_ate : null,
  };
}

async function loadSetup(repo: string): Promise<{
  repo: string;
  denied: boolean;
  snapshot: SetupSnapshot | null;
}> {
  const path = setupStatusPath(repo);
  if (!path) return { repo, denied: false, snapshot: null };
  try {
    const { data } = await api.get<unknown>(path);
    return { repo, denied: false, snapshot: toSnapshot(data) };
  } catch (err: unknown) {
    return { repo, denied: statusOf(err) === 403, snapshot: null };
  }
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mb-10" aria-label={title}>
      <h2 className="mb-3 text-lg font-semibold text-navy-900">{title}</h2>
      {children}
    </section>
  );
}

function ErrorBox({ message }: { message: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg bg-red-50 p-3 text-sm text-red-700" role="alert">
      <AlertCircle className="h-4 w-4 flex-shrink-0" aria-hidden="true" /> {message}
    </div>
  );
}

function Retry({ onRetry }: { onRetry: () => void }) {
  return (
    <button
      type="button"
      onClick={onRetry}
      className={`mt-3 text-sm font-medium text-navy-700 hover:text-navy-900 ${focusRing}`}
    >
      Try again
    </button>
  );
}

function PulseRows({ count, className }: { count: number; className: string }) {
  return (
    <div className="space-y-3" aria-hidden="true">
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className={`animate-pulse rounded-xl border border-gray-200 bg-white ${className}`} />
      ))}
    </div>
  );
}

function ledgerStateLabel(state: string): string {
  const trimmed = state.trim();
  if (!trimmed) return "";
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
}

function workLabel(state: string): string {
  if (state === "needs_maintainer_review") return "Needs maintainer review";
  if (state === "needs_review") return "Needs review";
  if (state === "awaiting_merge") return "Accepted — awaiting merge";
  return state.replaceAll("_", " ");
}

function EfficacyReviewsDue({ bounties, ready }: { bounties: Bounty[]; ready: boolean }) {
  const { context } = useWorkContext();
  const [items, setItems] = useState<EfficacyItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!ready) return;
    const dueBounties = bounties.filter((bounty) =>
      DUE_STATUSES.has(typeof bounty.status === "string" ? bounty.status.toLowerCase() : "")
    );
    if (dueBounties.length === 0) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    void (async () => {
      const due: EfficacyItem[] = [];
      let failed = 0;
      for (const bounty of dueBounties) {
        try {
          const { data } = await api.get<unknown>(`/bounties/${bounty.id}/submissions`);
          if (!Array.isArray(data)) {
            failed += 1;
            continue;
          }
          for (const sub of data) {
            if (!sub || typeof sub !== "object") continue;
            const row = sub as {
              id?: unknown;
              status?: unknown;
              score?: unknown;
              release_percent?: unknown;
              efficacy_check_at?: unknown;
              efficacy_criteria?: unknown;
            };
            if (row.status !== "partially_approved" || typeof row.id !== "string") continue;
            due.push({
              id: row.id,
              bounty_id: bounty.id,
              score: typeof row.score === "number" ? row.score : null,
              release_percent: typeof row.release_percent === "number" ? row.release_percent : null,
              efficacy_check_at: typeof row.efficacy_check_at === "string" ? row.efficacy_check_at : null,
              efficacy_criteria: typeof row.efficacy_criteria === "string" ? row.efficacy_criteria : null,
              bounty,
            });
          }
        } catch {
          failed += 1;
        }
      }
      if (cancelled) return;
      setItems(due);
      setError(failed === dueBounties.length ? "Couldn't load efficacy reviews." : "");
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [bounties, ready]);

  if (!ready || loading || (items.length === 0 && !error)) return null;

  return (
    <div className="mb-8">
      <h3 className="mb-3 flex items-center gap-2 text-lg font-semibold text-navy-900">
        <TrendingUp className="h-5 w-5 text-amber-500" aria-hidden="true" />
        Efficacy Reviews Due
      </h3>
      {error && <ErrorBox message={error} />}
      <div className="mt-3 space-y-3">
        {items.map((item) => (
          <Link
            key={item.id}
            href={withContext(`/dashboard/submissions/${item.id}`, context)}
            className={`block rounded-xl border border-amber-200 bg-amber-50 p-4 transition hover:bg-amber-100 ${focusRing}`}
          >
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-navy-900">
                  {item.bounty?.title || `Bounty ${item.bounty_id.slice(0, 8)}...`}
                </p>
                <p className="mt-0.5 text-xs text-amber-700">
                  {item.release_percent}% released (score: {item.score}) &middot; Holdback:{" "}
                  {100 - (item.release_percent || 0)}%
                </p>
                {item.efficacy_criteria && (
                  <p className="mt-0.5 text-xs text-amber-600">Criteria: {item.efficacy_criteria}</p>
                )}
              </div>
              <div className="ml-4 flex-shrink-0 text-right">
                {item.efficacy_check_at && (
                  <p className="text-xs text-amber-700">
                    Due: {new Date(item.efficacy_check_at).toLocaleDateString()}
                  </p>
                )}
                <span className="rounded-full bg-amber-200 px-2.5 py-1 text-xs font-medium text-amber-800">
                  Review
                </span>
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

function FunderRows({
  rows,
  context,
}: {
  rows: FunderLedgerRow[];
  context: WorkContext;
}) {
  if (rows.length === 0) {
    return <p className="text-sm text-gray-500">No funded GitHub bounties in the wallet yet.</p>;
  }
  return (
    <ul className="divide-y divide-gray-100 overflow-hidden rounded-xl border border-gray-200 bg-white">
      {rows.map((row) => {
        const title = row.title.trim() || `${row.repo}#${row.issueNumber || "?"}`;
        const issue = row.issueNumber > 0 ? `${row.repo}#${row.issueNumber}` : row.repo;
        const state = ledgerStateLabel(row.state);
        return (
          <li key={row.bountyId}>
            <Link
              href={withContext(`/bounties/${row.bountyId}`, context)}
              className={`block px-4 py-3 transition hover:bg-gray-50 ${focusRing}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-navy-900">{title}</p>
                  <p className="mt-0.5 text-xs text-gray-500">{issue}</p>
                </div>
                <div className="flex-shrink-0 text-right">
                  <p className="text-sm font-semibold text-navy-900 tabular-nums">
                    {row.amountAte === null ? (
                      <StatusBadge state="not_assessed" label="Not assessed" size="sm" />
                    ) : (
                      formatAteTest(row.amountAte)
                    )}
                  </p>
                  {row.feeAte !== null && (
                    <p className="mt-0.5 text-xs text-gray-500 tabular-nums">Fee {formatAteTest(row.feeAte)}</p>
                  )}
                  {state ? <p className="mt-0.5 text-xs capitalize text-gray-500">{state}</p> : null}
                </div>
              </div>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function BudgetBlock({ budget }: { budget: BudgetNumbers }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="rounded-xl border border-gray-200 bg-white p-4">
        <p className="text-xs text-gray-500">Funder headroom</p>
        {budget.headroomAte === null ? (
          <div className="mt-2">
            <StatusBadge
              state="not_assessed"
              label="Not assessed"
              reason="Headroom was not returned."
            />
          </div>
        ) : (
          <>
            <p className="mt-1 text-xl font-semibold text-navy-900">{formatAteTest(budget.headroomAte)}</p>
            {budget.minimumAte !== null && (
              <p className="mt-1 text-xs text-gray-500">
                A $1 bounty needs {formatAteTest(budget.minimumAte)}.
              </p>
            )}
          </>
        )}
      </div>
      <div className="rounded-xl border border-gray-200 bg-white p-4">
        <p className="text-xs text-gray-500">Alert-cap usage</p>
        {/* No GET returns alert-cap usage. Do not invent a number. */}
        <div className="mt-2">
          <StatusBadge
            state="not_assessed"
            label="Not assessed"
            reason="Alert-cap usage was not returned."
          />
        </div>
      </div>
    </div>
  );
}

function ApprovalList({ items, context }: { items: ApprovalTask[]; context: WorkContext }) {
  return (
    <ul className="space-y-3">
      {items.map((item) => {
        const title = item.title.trim() || `Bounty ${item.bountyId.slice(0, 8)}`;
        const repo = isRepoName(item.repo) ? item.repo : context.repo;
        return (
          <li key={item.bountyId}>
            <Link
              href={withContext(`/bounties/${item.bountyId}`, {
                view: context.view,
                repo,
                task: item.bountyId,
              })}
              className={`block rounded-xl border border-gray-200 bg-white p-4 transition hover:border-navy-300 ${focusRing}`}
            >
              <p className="text-sm font-medium text-navy-900">{title}</p>
              <p className="mt-1 text-xs text-gray-500">{item.repo}</p>
              <p className="mt-2 text-sm text-navy-800">{workLabel(item.workState)}</p>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

export default function FunderHome() {
  const { context } = useWorkContext();
  const contextRepo = isRepoName(context.repo) ? context.repo : null;
  const [reload, setReload] = useState(0);
  const [bounties, setBounties] = useState<AsyncList<Bounty>>(emptyList);
  const [wallet, setWallet] = useState<AsyncList<FunderLedgerRow>>(emptyList);
  const [picture, setPicture] = useState<Picture>({
    loading: true,
    reposError: "",
    approvalsError: "",
    model: null,
  });

  useEffect(() => {
    let cancelled = false;
    setBounties({ loading: true, error: "", items: [] });
    setWallet({ loading: true, error: "", items: [] });
    setPicture({ loading: true, reposError: "", approvalsError: "", model: null });

    // GETs only. The header switch changes the view and must not mutate.
    void (async () => {
      const postedPromise = api
        .get<unknown>("/bounties/my/posted")
        .then(({ data }) => {
          if (!Array.isArray(data)) {
            return { ok: false as const, error: "Couldn't load your bounties.", items: [] as Bounty[] };
          }
          return { ok: true as const, error: "", items: data as Bounty[] };
        })
        .catch((err: unknown) => ({
          ok: false as const,
          error: messageFrom(err, "Couldn't load your bounties."),
          items: [] as Bounty[],
        }));

      const ledgerPromise = api
        .get<unknown>("/v1/me/ledger")
        .then(({ data }) => {
          const ledger = readLedgerFunder(data);
          if (!ledger) {
            return {
              ok: false as const,
              error: "Couldn't load your wallet.",
              items: [] as FunderLedgerRow[],
              freeAte: null,
              exchangeFailed: true,
            };
          }
          return {
            ok: true as const,
            error: "",
            items: ledger.rows,
            freeAte: ledger.freeAte,
            exchangeFailed: ledger.exchangeFailed,
          };
        })
        .catch((err: unknown) => ({
          ok: false as const,
          error: messageFrom(err, "Couldn't load your wallet."),
          items: [] as FunderLedgerRow[],
          freeAte: null,
          exchangeFailed: true,
        }));

      const cyberPromise = loadCyber()
        .then((result) => ({ ok: true as const, error: "", ...result }))
        .catch((err: unknown) => ({
          ok: false as const,
          error: messageFrom(err, "Couldn't load tasks waiting on you."),
          tasks: [] as CyberHit[],
          complete: false,
        }));

      // 404 means self-serve onboarding is off, so nothing is linked through it.
      const linkedPromise = api
        .get<unknown>("/v1/github/repos/mine")
        .then(({ data }) => readLinkedRepos(data))
        .catch((err: unknown) => (statusOf(err) === 404 ? [] : null));

      const [posted, ledger, cyber, linked] = await Promise.all([
        postedPromise,
        ledgerPromise,
        cyberPromise,
        linkedPromise,
      ]);
      if (cancelled) return;
      setBounties({ loading: false, error: posted.error, items: posted.items });
      setWallet({ loading: false, error: ledger.error, items: ledger.items });

      try {
        const owned = mergeOwned({
          ledgerRows: ledger.ok ? ledger.items : null,
          posted: posted.ok
            ? posted.items.map((bounty) => ({
                id: bounty.id,
                title: bounty.title,
                status: bounty.status,
                tags: bounty.tags,
              }))
            : null,
        });
        const ownedIds = new Set(owned.owned.map((bounty) => bounty.bountyId));
        const cyberRepos = cyber.ok
          ? cyber.tasks.filter((task) => ownedIds.has(task.bountyId)).map((task) => task.repo)
          : [];
        const repoNames = uniqueRepos([
          ...(linked ?? []),
          ...owned.owned.map((bounty) => bounty.repo),
          ...cyberRepos,
          contextRepo,
        ]);
        const setups = await Promise.all(repoNames.map((repo) => loadSetup(repo)));
        if (cancelled) return;

        const model = buildFunderModel({
          contextRepo,
          linked,
          ownedKnown: owned.ownedKnown,
          owned: owned.owned,
          cyber: cyber.ok ? cyber.tasks : null,
          cyberComplete: cyber.ok && cyber.complete,
          setups,
          ledgerFreeAte: ledger.ok ? ledger.freeAte : null,
          ledgerExchangeFailed: !ledger.ok || ledger.exchangeFailed,
        });
        const discoveryFailed = !posted.ok && !ledger.ok && model.repos.length === 0;
        setPicture({
          loading: false,
          reposError: discoveryFailed ? "Couldn't load your repositories." : "",
          approvalsError: cyber.ok ? "" : cyber.error,
          model,
        });
      } catch (err: unknown) {
        if (cancelled) return;
        const message = messageFrom(err, "Couldn't load your repositories.");
        setPicture({
          loading: false,
          reposError: message,
          approvalsError: cyber.error || message,
          model: {
            repos: [],
            approvals: [],
            budget: {
              headroomAte: ledger.ok && !ledger.exchangeFailed ? ledger.freeAte : null,
              minimumAte: null,
            },
            tasksIncomplete: false,
            discoveryComplete: false,
          },
        });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [contextRepo, reload]);

  const retry = () => setReload((value) => value + 1);
  const model = picture.model;
  const cards = model
    ? sortRepoCards(
        model.repos.map((repo) => ({
          repo: repo.repo,
          status: repo.status,
          reason: repo.reason,
          meta: repo.meta,
          href: withContext("/dashboard/github/setup", { ...context, repo: repo.repo }),
        }))
      )
    : [];

  return (
    <div>
      <Panel title="My repositories">
        {picture.loading ? (
          <>
            <p className="sr-only">Loading repositories</p>
            <PulseRows count={2} className="h-24" />
          </>
        ) : picture.reposError ? (
          <div>
            <ErrorBox message={picture.reposError} />
            <Retry onRetry={retry} />
          </div>
        ) : cards.length === 0 ? (
          <div className="rounded-xl border border-gray-200 bg-white px-4 py-10 text-center">
            <p className="font-medium text-navy-900">No linked repositories yet</p>
            <p className="mt-1 text-sm text-gray-500">
              Connect a repository to fund tasks on its security alerts.
            </p>
            <Link
              href={withContext("/dashboard/github/setup", context)}
              className={`mt-4 inline-flex items-center rounded-lg bg-money px-4 py-2 text-sm font-semibold text-navy-900 transition hover:bg-money-dark ${focusRing}`}
            >
              Connect a repository
            </Link>
          </div>
        ) : (
          <>
            {model && !model.discoveryComplete && (
              <p className="mb-3 text-sm text-amber-800">
                Some repositories could not be listed.
              </p>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              {cards.map((card) => (
                <RepoCard key={card.repo} {...card} />
              ))}
            </div>
          </>
        )}
      </Panel>

      <section className="mb-10" aria-label="Funding">
        <h2 className="mb-3 text-lg font-semibold text-navy-900">Funding</h2>
        <EfficacyReviewsDue
          key={reload}
          bounties={bounties.items}
          ready={!bounties.loading && !bounties.error}
        />
        <div className="mb-6 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-navy-900">My Bounties</h3>
          <Link
            href={withContext("/bounties/new", context)}
            className={`rounded-lg bg-money px-4 py-2 text-sm font-semibold text-navy-900 transition hover:bg-money-dark ${focusRing}`}
          >
            Post New Bounty
          </Link>
        </div>
        {bounties.loading ? (
          <>
            <p className="sr-only">Loading bounties</p>
            <PulseRows count={3} className="h-32" />
          </>
        ) : bounties.error ? (
          <div>
            <ErrorBox message={bounties.error} />
            <Retry onRetry={retry} />
          </div>
        ) : bounties.items.length === 0 ? (
          <div className="py-16 text-center text-gray-400">
            <FileText className="mx-auto mb-3 h-12 w-12 opacity-50" aria-hidden="true" />
            <p className="font-medium">No bounties yet</p>
            <p className="mt-1 text-sm">Post your first bounty to get started</p>
          </div>
        ) : (
          <div className="space-y-3">
            {bounties.items.map((bounty) => (
              <BountyCard key={bounty.id} bounty={bounty} />
            ))}
          </div>
        )}

        <div className="mb-3 mt-8 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-navy-900">Funder rows</h3>
          <Link
            href={withContext("/dashboard/wallet", context)}
            className={`text-sm font-medium text-navy-700 hover:text-navy-900 ${focusRing}`}
          >
            Open wallet
          </Link>
        </div>
        {wallet.loading ? (
          <>
            <p className="sr-only">Loading funder rows</p>
            <PulseRows count={2} className="h-16" />
          </>
        ) : wallet.error ? (
          <div>
            <ErrorBox message={wallet.error} />
            <Retry onRetry={retry} />
          </div>
        ) : (
          <FunderRows rows={wallet.items} context={context} />
        )}
      </section>

      <Panel title="Budgets">
        {picture.loading || !model ? (
          <>
            <p className="sr-only">Loading budgets</p>
            <PulseRows count={2} className="h-24" />
          </>
        ) : (
          <BudgetBlock budget={model.budget} />
        )}
      </Panel>

      <Panel title="Approvals waiting on you">
        {picture.loading ? (
          <>
            <p className="sr-only">Loading approvals</p>
            <PulseRows count={2} className="h-20" />
          </>
        ) : picture.approvalsError ? (
          <div>
            <ErrorBox message={picture.approvalsError} />
            <Retry onRetry={retry} />
          </div>
        ) : (
          <>
            {model && model.approvals.length > 0 ? (
              <ApprovalList items={model.approvals} context={context} />
            ) : (
              <p className="text-sm text-gray-500">
                {model?.tasksIncomplete
                  ? "No loaded task is waiting on you."
                  : "Nothing is waiting on you."}
              </p>
            )}
            {model?.tasksIncomplete && (
              <div className="mt-3">
                <StatusBadge
                  state="not_assessed"
                  label="Not assessed"
                  reason="Some tasks were not loaded."
                />
              </div>
            )}
          </>
        )}
      </Panel>
    </div>
  );
}
