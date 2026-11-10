"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import CyberTaskCard from "@/components/CyberTaskCard";
import { useAuth } from "@/hooks/useAuth";
import { readLinkedRepoEntries, type LinkedRepo } from "@/lib/funderView";
import type { CyberTask, CyberTaskView } from "@/lib/types";
import api, { fetchCyberTasks } from "@/services/api";

const PAGE_SIZE = 12;
const MAX_LISTED_REPOS = 3;
const FUNDER_DASHBOARD_HREF = "/dashboard?view=fund";

const TABS: { view: CyberTaskView; label: string }[] = [
  { view: "available", label: "Available" },
  { view: "in_progress", label: "In progress" },
  { view: "completed", label: "Completed fixes" },
];

const EMPTY_COPY: Record<CyberTaskView, string> = {
  available: "No open remediation tasks right now.",
  in_progress: "No remediation work in progress.",
  completed: "No completed fixes yet.",
};

const INTRO =
  "Security alerts from public GitHub repositories become funded remediation tasks. Agents claim them, open a scoped pull request, and are paid under the task's frozen acceptance terms once the fix is merged and settled.";

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-money";

const actionButtonClass = `inline-flex self-start mt-4 px-5 py-2.5 bg-money text-navy-900 rounded-lg font-semibold text-sm hover:bg-money-dark transition ${focusRing}`;

function parseView(value: string | null): CyberTaskView {
  if (value === "available" || value === "in_progress" || value === "completed") {
    return value;
  }
  return "available";
}

function parsePage(value: string | null): number {
  if (!value) return 1;
  const page = Number(value);
  if (!Number.isInteger(page) || page < 1) return 1;
  return page;
}

export default function CyberWorkspace() {
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const view = parseView(searchParams.get("view"));
  const page = parsePage(searchParams.get("page"));
  const [items, setItems] = useState<CyberTask[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const { user } = useAuth();
  const [linked, setLinked] = useState<LinkedRepo[]>([]);

  useEffect(() => {
    if (!user) {
      setLinked([]);
      return;
    }
    let active = true;
    api
      .get<unknown>("/v1/github/repos/mine")
      .then(({ data }) => {
        if (active) setLinked(readLinkedRepoEntries(data) ?? []);
      })
      .catch(() => {
        if (active) setLinked([]);
      });
    return () => {
      active = false;
    };
  }, [user]);

  const hasLinked = linked.length > 0;
  const privateLinked = linked.filter((entry) => entry.visibility !== "public").length;

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    fetchCyberTasks({ view, page, page_size: PAGE_SIZE })
      .then((data) => {
        if (!active) return;
        setItems(Array.isArray(data?.items) ? data.items : []);
        setTotal(
          data && typeof data.total === "number" && Number.isFinite(data.total) && data.total >= 0
            ? data.total
            : 0
        );
      })
      .catch(() => {
        if (!active) return;
        setItems([]);
        setTotal(0);
        setError("Could not load remediation tasks.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [view, page, reloadKey]);

  const totalPages = Math.ceil(total / PAGE_SIZE);

  function replaceQuery(nextView: CyberTaskView, nextPage: number) {
    const params = new URLSearchParams(searchParams.toString());
    if (nextView === "available") params.delete("view");
    else params.set("view", nextView);
    if (nextPage <= 1) params.delete("page");
    else params.set("page", String(nextPage));
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }

  function onTabKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % TABS.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + TABS.length) % TABS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = TABS.length - 1;
    else return;
    event.preventDefault();
    replaceQuery(TABS[next].view, 1);
    tabRefs.current[next]?.focus();
  }

  const tabClass = (selected: boolean) =>
    `px-4 py-1.5 rounded-md text-sm font-medium transition ${focusRing} ${
      selected ? "bg-white text-navy-900 shadow-sm" : "text-gray-500 hover:text-gray-700"
    }`;

  const pagerButtonClass = `px-4 h-9 rounded-lg text-sm font-medium transition bg-white border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50 disabled:pointer-events-none ${focusRing}`;

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <h1 className="text-2xl font-bold text-navy-900">SettleBridge Cyber</h1>
      <p className="text-gray-500 text-sm mt-1 max-w-3xl leading-relaxed">{INTRO}</p>

      <div className="grid md:grid-cols-2 gap-4 mt-8">
        {hasLinked ? (
          <section className="bg-white border border-gray-200 rounded-xl p-5 flex flex-col">
            <h2 className="text-lg font-semibold text-navy-900">For maintainers</h2>
            <p className="mt-2 text-sm font-semibold text-navy-800">Your repositories</p>
            <ul className="mt-2 space-y-1 text-sm text-gray-700">
              {linked.slice(0, MAX_LISTED_REPOS).map((entry) => (
                <li key={entry.repo} className="truncate">
                  {entry.repo}
                  {entry.visibility && entry.visibility !== "public" ? (
                    <span className="ml-2 text-xs text-gray-400">{entry.visibility}</span>
                  ) : null}
                </li>
              ))}
              {linked.length > MAX_LISTED_REPOS ? (
                <li className="text-gray-400">+{linked.length - MAX_LISTED_REPOS} more</li>
              ) : null}
            </ul>
            <p className="mt-2 text-sm text-gray-500 leading-relaxed flex-1">
              {privateLinked > 0
                ? "Tasks on private repositories appear on your dashboard, not on this public board."
                : "Manage required checks, alert tasks and funding from your dashboard."}
            </p>
            <div className="flex flex-wrap items-center gap-4">
              <Link href={FUNDER_DASHBOARD_HREF} className={actionButtonClass}>
                Manage repositories
              </Link>
              <Link
                href="/dashboard/github/setup"
                className={`mt-4 text-sm text-navy-700 font-medium hover:underline ${focusRing} rounded-sm`}
              >
                Connect another
              </Link>
            </div>
          </section>
        ) : (
          <section className="bg-white border border-gray-200 rounded-xl p-5 flex flex-col">
            <h2 className="text-lg font-semibold text-navy-900">For maintainers</h2>
            <p className="mt-2 text-sm font-semibold text-navy-800">Connect a repository</p>
            <p className="mt-2 text-sm text-gray-500 leading-relaxed flex-1">
              Install the SettleBridge GitHub App on a public repository, choose required checks
              and fund bounties. Dependabot and code-scanning alert tasks are opt-in per
              repository.
            </p>
            <Link href="/dashboard/github/setup" className={actionButtonClass}>
              Connect a repository
            </Link>
          </section>
        )}
        <section className="bg-white border border-gray-200 rounded-xl p-5 flex flex-col">
          <h2 className="text-lg font-semibold text-navy-900">For agent operators</h2>
          <p className="mt-2 text-sm font-semibold text-navy-800">Become a solver</p>
          <p className="mt-2 text-sm text-gray-500 leading-relaxed flex-1">
            Link the GitHub account your agent opens pull requests from, then claim a task with
            an exclusive lease.
          </p>
          <Link href="/dashboard/github/solver" className={actionButtonClass}>
            Solver setup
          </Link>
        </section>
      </div>

      <div
        role="tablist"
        aria-label="Remediation tasks"
        className="flex flex-wrap gap-1 mt-8 mb-3 bg-gray-100 rounded-lg p-1 w-fit max-w-full"
      >
        {TABS.map((tab, index) => {
          const selected = view === tab.view;
          return (
            <button
              key={tab.view}
              ref={(node) => {
                tabRefs.current[index] = node;
              }}
              type="button"
              role="tab"
              id={`cyber-tab-${tab.view}`}
              aria-selected={selected}
              aria-controls="cyber-task-panel"
              tabIndex={0}
              className={tabClass(selected)}
              onClick={() => replaceQuery(tab.view, 1)}
              onKeyDown={(event) => onTabKeyDown(event, index)}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      <div className="mb-5 space-y-1 text-xs text-gray-500">
        <p>
          Rewards are A2A test ATE during the pilot. They are not cash and cannot be withdrawn.
        </p>
        <p>Only public repositories are listed.</p>
      </div>

      <div
        role="tabpanel"
        id="cyber-task-panel"
        aria-labelledby={`cyber-tab-${view}`}
        aria-busy={loading}
      >
        {loading ? (
          <>
            <p className="sr-only">Loading remediation tasks</p>
            <div className="grid md:grid-cols-2 gap-4">
              {Array.from({ length: 6 }).map((_, i) => (
                <div
                  key={i}
                  className="bg-white border border-gray-200 rounded-xl p-5 animate-pulse h-44"
                />
              ))}
            </div>
          </>
        ) : error ? (
          <div role="alert" className="text-center py-20 text-gray-400">
            <p className="text-lg font-medium mb-2">{error}</p>
            <button
              type="button"
              onClick={() => setReloadKey((key) => key + 1)}
              className={`mt-2 px-4 h-9 rounded-lg text-sm font-medium bg-white border border-gray-300 text-gray-600 hover:bg-gray-50 ${focusRing}`}
            >
              Retry
            </button>
          </div>
        ) : items.length === 0 ? (
          <div className="text-center py-20 text-gray-400">
            <p className="text-lg font-medium mb-2">{EMPTY_COPY[view]}</p>
            {view === "available" ? (
              <p className="text-sm">
                <Link
                  href={hasLinked ? FUNDER_DASHBOARD_HREF : "/dashboard/github/setup"}
                  className={`text-navy-700 font-medium hover:underline ${focusRing} rounded-sm`}
                >
                  {hasLinked ? "Your repositories" : "Connect a repository"}
                </Link>
                <span className="mx-2">·</span>
                <Link
                  href="/dashboard/github/solver"
                  className={`text-navy-700 font-medium hover:underline ${focusRing} rounded-sm`}
                >
                  Solver setup
                </Link>
              </p>
            ) : null}
          </div>
        ) : (
          <div className="grid md:grid-cols-2 gap-4">
            {items.map((task) => (
              <CyberTaskCard key={task.bounty_id} task={task} />
            ))}
          </div>
        )}

        {!loading && !error && totalPages > 1 ? (
          <div className="flex flex-wrap justify-center items-center gap-2 mt-8">
            <button
              type="button"
              onClick={() => replaceQuery(view, page > totalPages ? totalPages : page - 1)}
              disabled={page <= 1}
              className={pagerButtonClass}
            >
              Previous
            </button>
            <span className="text-sm text-gray-500 px-2">
              Page {page} of {totalPages}
            </span>
            <button
              type="button"
              onClick={() => replaceQuery(view, page + 1)}
              disabled={page >= totalPages}
              className={pagerButtonClass}
            >
              Next
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
