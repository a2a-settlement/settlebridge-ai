"use client";

import { useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import {
  AlertCircle,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  Key,
  MoreVertical,
  PauseCircle,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import {
  groupAgentsByDeveloperId,
  useManagedAgents,
  type ManagedAgentsState,
} from "@/hooks/useManagedAgents";
import {
  extractApiError,
  formatActivityDate,
  truncateAgentId,
  visibleManagedAgents,
  type ManagedAgent,
  type RotatedKeyResult,
} from "@/lib/managedAgents";
import ReputationScore from "@/components/ReputationScore";
import RotatedKeyReveal from "@/components/agents/RotatedKeyReveal";
import RegisterAgentModal from "@/components/agents/RegisterAgentModal";
import AddDeveloperIdModal from "@/components/agents/AddDeveloperIdModal";
import PublishAgentCardModal from "@/components/agents/PublishAgentCardModal";
import api from "@/services/api";

type PendingAction =
  | { kind: "suspend"; agent: ManagedAgent }
  | { kind: "unsuspend"; agent: ManagedAgent }
  | { kind: "rotate"; agent: ManagedAgent }
  | { kind: "bulk-suspend"; count: number }
  | { kind: "bulk-unsuspend"; count: number };

export interface MyAgentsProps {
  managed?: ManagedAgentsState;
}

function StatusPill({ status }: { status: string }) {
  const active = status === "active";
  return (
    <span
      className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium capitalize ${
        active
          ? "bg-green-100 text-green-800"
          : "bg-gray-100 text-gray-700"
      }`}
    >
      {active ? (
        <CheckCircle2 className="w-3 h-3" aria-hidden="true" />
      ) : (
        <PauseCircle className="w-3 h-3" aria-hidden="true" />
      )}
      {status || "unknown"}
    </span>
  );
}

function AgentCardPill({ hasCard }: { hasCard?: boolean }) {
  if (hasCard) {
    return (
      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium bg-green-50 text-green-800 border border-green-200">
        <CheckCircle2 className="w-3 h-3" aria-hidden="true" />
        Published
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium bg-amber-50 text-amber-800 border border-amber-200">
      <AlertCircle className="w-3 h-3" aria-hidden="true" />
      Missing
    </span>
  );
}

function RunsBehindBadge({ agent }: { agent: ManagedAgent }) {
  const claims = agent.gateway_claims;
  if (!claims || claims.length === 0) return null;
  const label = claims
    .map((c) => c.gateway_name || truncateAgentId(c.gateway_id))
    .join(", ");
  return (
    <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-navy-50 text-navy-800 border border-navy-200">
      <ShieldCheck className="w-3 h-3 flex-shrink-0" aria-hidden="true" />
      <span>Runs behind: {label}</span>
    </span>
  );
}

function IdCopy({ id }: { id: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className="inline-flex items-center gap-1 font-mono text-xs text-gray-500">
      <span title={id}>{truncateAgentId(id)}</span>
      <button
        type="button"
        title="Copy agent ID"
        aria-label={`Copy agent ID ${id}`}
        className="text-gray-400 hover:text-navy-700 rounded focus:outline-none focus:ring-2 focus:ring-navy-500"
        onClick={() => {
          void navigator.clipboard.writeText(id);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? (
          <Check className="w-3 h-3 text-money-dark" />
        ) : (
          <Copy className="w-3 h-3" />
        )}
      </button>
    </span>
  );
}

function AgentRowMenu({
  agent,
  busy,
  onPublishCard,
  onSuspend,
  onUnsuspend,
  onRotate,
}: {
  agent: ManagedAgent;
  busy: boolean;
  onPublishCard: () => void;
  onSuspend: () => void;
  onUnsuspend: () => void;
  onRotate: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        type="button"
        disabled={busy}
        onClick={() => setOpen(!open)}
        className="p-1.5 rounded-lg text-gray-500 hover:bg-gray-100 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-navy-500"
        aria-label={`Actions for ${agent.bot_name}`}
        aria-expanded={open}
      >
        <MoreVertical className="w-4 h-4" />
      </button>
      {open && (
        <>
          <button
            type="button"
            className="fixed inset-0 z-10 cursor-default"
            aria-label="Close menu"
            onClick={() => setOpen(false)}
          />
          <div className="absolute right-0 z-20 mt-1 w-48 bg-white border border-gray-200 rounded-lg shadow-lg py-1 text-sm">
            <button
              type="button"
              className="w-full text-left px-3 py-2 text-navy-900 hover:bg-gray-50 font-medium"
              onClick={() => {
                setOpen(false);
                onPublishCard();
              }}
            >
              {agent.has_agent_card ? "Edit Agent Card" : "Publish Agent Card"}
            </button>
            <button
              type="button"
              className="w-full text-left px-3 py-2 text-navy-800 hover:bg-gray-50"
              onClick={() => {
                setOpen(false);
                onRotate();
              }}
            >
              Rotate key
            </button>
            {agent.status === "active" ? (
              <button
                type="button"
                className="w-full text-left px-3 py-2 text-red-600 hover:bg-red-50"
                onClick={() => {
                  setOpen(false);
                  onSuspend();
                }}
              >
                Suspend
              </button>
            ) : (
              <button
                type="button"
                className="w-full text-left px-3 py-2 text-green-700 hover:bg-green-50"
                onClick={() => {
                  setOpen(false);
                  onUnsuspend();
                }}
              >
                Reactivate
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

export default function MyAgents({ managed }: MyAgentsProps) {
  const { user, loading: authLoading } = useAuth();
  const linked = !!user?.exchange_bot_id;
  const fallbackManaged = useManagedAgents(!managed && linked);
  const {
    bots,
    data,
    developerIds,
    loading,
    error,
    refresh,
  } = managed ?? fallbackManaged;

  const [search, setSearch] = useState("");
  const [showSuspended, setShowSuspended] = useState(false);
  const [devIdFilter, setDevIdFilter] = useState<string>("all");
  const [hasCardOnly, setHasCardOnly] = useState(false);
  const [showTest, setShowTest] = useState(false);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(
    new Set()
  );

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [acting, setActing] = useState(false);
  const [actionError, setActionError] = useState("");
  const [rotatedKey, setRotatedKey] = useState<RotatedKeyResult | null>(null);

  const [registerModalDevId, setRegisterModalDevId] = useState<
    string | null | undefined
  >(undefined);
  const [attachDevModalOpen, setAttachDevModalOpen] = useState(false);
  const [cardModalAgent, setCardModalAgent] = useState<ManagedAgent | null>(
    null
  );

  const roster = useMemo(
    () =>
      visibleManagedAgents(bots, user?.exchange_bot_id, { includeTest: true }),
    [bots, user?.exchange_bot_id]
  );
  const listAgents = useMemo(
    () => (showTest ? roster : roster.filter((a) => a.is_test !== true)),
    [roster, showTest]
  );

  const allDeveloperIds = useMemo(() => {
    const out: string[] = [];
    const seen = new Set<string>();
    const add = (id: string | undefined | null) => {
      if (!id || seen.has(id)) return;
      seen.add(id);
      out.push(id);
    };
    for (const id of developerIds) add(id);
    for (const a of roster) add(a.developer_id);
    return out;
  }, [developerIds, roster]);

  const scopedAgents = useMemo(() => {
    const q = search.trim().toLowerCase();
    return listAgents.filter((a) => {
      if (devIdFilter !== "all" && a.developer_id !== devIdFilter) return false;
      if (hasCardOnly && !a.has_agent_card) return false;
      if (!q) return true;
      return (
        a.bot_name?.toLowerCase().includes(q) ||
        a.id.toLowerCase().includes(q) ||
        a.developer_id?.toLowerCase().includes(q)
      );
    });
  }, [listAgents, devIdFilter, hasCardOnly, search]);

  const suspendedCount = useMemo(
    () => scopedAgents.filter((a) => a.status === "suspended").length,
    [scopedAgents]
  );

  const filtered = useMemo(
    () =>
      scopedAgents.filter((a) =>
        a.status === "suspended" ? showSuspended : a.status === "active"
      ),
    [scopedAgents, showSuspended]
  );

  const hasRestrictiveAgentFilter = search.trim() !== "" || hasCardOnly;

  const groups = useMemo(() => {
    const seedIds = hasRestrictiveAgentFilter
      ? undefined
      : devIdFilter === "all"
        ? allDeveloperIds
        : allDeveloperIds.includes(devIdFilter)
          ? [devIdFilter]
          : undefined;
    return groupAgentsByDeveloperId(filtered, seedIds);
  }, [filtered, hasRestrictiveAgentFilter, devIdFilter, allDeveloperIds]);

  const toggleGroupCollapsed = (devId: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(devId)) next.delete(devId);
      else next.add(devId);
      return next;
    });
  };

  if (authLoading) {
    return (
      <div className="space-y-3" aria-busy="true" aria-live="polite">
        <div className="h-8 w-48 bg-gray-100 rounded-lg animate-pulse" />
        <div className="h-24 bg-gray-100 rounded-xl animate-pulse" />
        <div className="h-24 bg-gray-100 rounded-xl animate-pulse" />
      </div>
    );
  }

  if (!user) {
    return (
      <div className="text-center py-16 px-4">
        <Bot className="w-12 h-12 mx-auto mb-4 text-gray-300" />
        <p className="text-gray-600 mb-4">
          Sign in to manage your settlement agents.
        </p>
        <Link
          href="/login?next=/agents%3Ftab%3Dmine"
          className="inline-flex px-5 py-2.5 bg-money text-navy-900 rounded-lg text-sm font-semibold hover:bg-money-dark transition focus:outline-none focus:ring-2 focus:ring-navy-500"
        >
          Sign in
        </Link>
      </div>
    );
  }

  if (!linked) {
    return (
      <div className="text-center py-16 px-4 max-w-md mx-auto">
        <p className="text-gray-600 mb-4">
          Link a settlement exchange account in Settings before you can list managed agents.
        </p>
        <Link
          href="/dashboard/settings"
          className="inline-flex px-5 py-2.5 bg-navy-900 text-white rounded-lg text-sm font-semibold hover:bg-navy-800 transition focus:outline-none focus:ring-2 focus:ring-navy-500"
        >
          Open Settings
        </Link>
      </div>
    );
  }

  const toggleSelected = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const runConfirm = async () => {
    if (!pending) return;
    setActing(true);
    setActionError("");
    try {
      if (pending.kind === "rotate") {
        const res = await api.post<RotatedKeyResult>(
          `/bots/${encodeURIComponent(pending.agent.id)}/rotate-key`
        );
        setRotatedKey(res.data);
        setPending(null);
        await refresh();
        return;
      }
      if (pending.kind === "suspend") {
        await api.post(`/bots/${encodeURIComponent(pending.agent.id)}/suspend`);
      } else if (pending.kind === "unsuspend") {
        await api.post(
          `/bots/${encodeURIComponent(pending.agent.id)}/unsuspend`
        );
      } else if (pending.kind === "bulk-suspend") {
        const targets = filtered.filter(
          (a) => selected.has(a.id) && a.status === "active"
        );
        await Promise.all(
          targets.map((a) =>
            api.post(`/bots/${encodeURIComponent(a.id)}/suspend`)
          )
        );
        setSelected(new Set());
      } else if (pending.kind === "bulk-unsuspend") {
        const targets = filtered.filter(
          (a) => selected.has(a.id) && a.status === "suspended"
        );
        await Promise.all(
          targets.map((a) =>
            api.post(`/bots/${encodeURIComponent(a.id)}/unsuspend`)
          )
        );
        setSelected(new Set());
      }
      setPending(null);
      await refresh();
    } catch (err: unknown) {
      setActionError(extractApiError(err, "Action failed"));
    } finally {
      setActing(false);
    }
  };

  const confirmMessage = () => {
    if (!pending) return "";
    if (pending.kind === "suspend") {
      return `Suspend "${pending.agent.bot_name}"? New escrows and claims will stop until you reactivate this agent.`;
    }
    if (pending.kind === "unsuspend") {
      return `Reactivate "${pending.agent.bot_name}"? The agent can transact on the exchange again.`;
    }
    if (pending.kind === "rotate") {
      return `Rotate the API key for "${pending.agent.bot_name}"? The old key keeps working for a short grace period, then stops.`;
    }
    if (pending.kind === "bulk-suspend") {
      return `Suspend ${pending.count} selected agent(s)? They cannot start new escrows until reactivated.`;
    }
    if (pending.kind === "bulk-unsuspend") {
      return `Reactivate ${pending.count} selected agent(s)?`;
    }
    return "";
  };

  const renderAgentRow = (agent: ManagedAgent) => {
    const checked = selected.has(agent.id);
    const activityText = formatActivityDate(
      agent.last_activity_at ?? agent.created_at
    );
    return (
      <div
        key={agent.id}
        className="border border-gray-200 rounded-xl p-4 flex flex-col gap-3 bg-white"
      >
        <div className="flex items-start gap-3">
          <input
            type="checkbox"
            checked={checked}
            onChange={() => toggleSelected(agent.id)}
            className="mt-1 flex-shrink-0 rounded focus:ring-2 focus:ring-navy-500"
            aria-label={`Select ${agent.bot_name}`}
          />
          <div className="flex-1 min-w-0 space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <Link
                href={`/agents/${agent.id}`}
                className="font-semibold text-navy-900 hover:text-navy-700 text-sm focus:outline-none focus:ring-2 focus:ring-navy-500 rounded"
              >
                {agent.bot_name}
              </Link>
              <StatusPill status={agent.status} />
              <AgentCardPill hasCard={agent.has_agent_card} />
              {agent.is_test && (
                <span className="text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-800 border border-amber-200">
                  test
                </span>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <IdCopy id={agent.id} />
              <RunsBehindBadge agent={agent} />
            </div>
            <p className="text-xs text-gray-500">
              Last activity: {activityText}
            </p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <ReputationScore score={agent.reputation ?? null} size="sm" />
            <AgentRowMenu
              agent={agent}
              busy={acting}
              onPublishCard={() => setCardModalAgent(agent)}
              onSuspend={() => setPending({ kind: "suspend", agent })}
              onUnsuspend={() => setPending({ kind: "unsuspend", agent })}
              onRotate={() => setPending({ kind: "rotate", agent })}
            />
          </div>
        </div>
      </div>
    );
  };

  const showOnboardingEmpty =
    data != null &&
    roster.length === 0 &&
    allDeveloperIds.length <= 1 &&
    !hasRestrictiveAgentFilter;

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <div>
          <h2 className="text-lg font-bold text-navy-900">My agents</h2>
          <p className="text-sm text-gray-500">
            Agents you control on the settlement exchange
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() =>
              setRegisterModalDevId(
                devIdFilter !== "all" ? devIdFilter : (allDeveloperIds[0] ?? null)
              )
            }
            className="inline-flex items-center gap-1.5 px-4 py-2 bg-money text-navy-900 rounded-lg text-sm font-semibold hover:bg-money-dark transition focus:outline-none focus:ring-2 focus:ring-navy-500"
          >
            <Plus className="w-4 h-4" />
            Register agent
          </button>
          <button
            type="button"
            onClick={() => setAttachDevModalOpen(true)}
            className="inline-flex items-center gap-1.5 px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-navy-800 hover:bg-gray-50 transition focus:outline-none focus:ring-2 focus:ring-navy-500"
          >
            Add Developer ID
          </button>
        </div>
      </div>

      {user.exchange_bot_id && (
        <div className="bg-navy-900 text-white rounded-xl p-4 mb-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div>
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">
              Your account
            </p>
            <p className="font-semibold">Linked exchange identity</p>
            <p
              className="font-mono text-xs text-gray-300 mt-1"
              title={user.exchange_bot_id}
            >
              {truncateAgentId(user.exchange_bot_id)}
            </p>
          </div>
          <Link
            href="/dashboard/settings"
            className="text-sm font-medium text-money hover:text-money-dark focus:outline-none focus:ring-2 focus:ring-money rounded px-1"
          >
            Settings →
          </Link>
        </div>
      )}

      {/* Search + dropdown filters */}
      <div className="flex flex-col lg:flex-row lg:items-center gap-3 mb-4">
        <div className="relative flex-1 max-w-md">
          <label htmlFor="my-agents-search" className="sr-only">
            Search agents by name or ID
          </label>
          <Search
            className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400"
            aria-hidden="true"
          />
          <input
            id="my-agents-search"
            type="search"
            placeholder="Search by name or ID…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-10 pr-4 py-2 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm"
          />
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <div className="flex items-center gap-1.5">
            <label
              htmlFor="my-agents-dev-filter"
              className="text-xs font-medium text-gray-600"
            >
              Developer ID
            </label>
            <select
              id="my-agents-dev-filter"
              value={devIdFilter}
              onChange={(e) => setDevIdFilter(e.target.value)}
              className="px-3 py-2 rounded-lg border border-gray-300 bg-white text-sm font-mono text-navy-900 max-w-[220px] truncate focus:outline-none focus:ring-2 focus:ring-navy-500"
            >
              <option value="all">All Developer IDs</option>
              {allDeveloperIds.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          </div>

          <button
            type="button"
            onClick={() => void refresh()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm text-navy-700 hover:bg-gray-100 font-medium focus:outline-none focus:ring-2 focus:ring-navy-500"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
      </div>

      <div className="flex flex-wrap gap-5 text-sm mb-5">
        <label
          htmlFor="my-agents-has-card"
          className="inline-flex items-center gap-2 text-gray-600 cursor-pointer"
        >
          <input
            id="my-agents-has-card"
            type="checkbox"
            checked={hasCardOnly}
            onChange={(e) => setHasCardOnly(e.target.checked)}
            className="rounded focus:ring-2 focus:ring-navy-500"
          />
          Has Agent Card
        </label>
        <label
          htmlFor="my-agents-show-test"
          className="inline-flex items-center gap-2 text-gray-600 cursor-pointer"
        >
          <input
            id="my-agents-show-test"
            type="checkbox"
            checked={showTest}
            onChange={(e) => setShowTest(e.target.checked)}
            className="rounded focus:ring-2 focus:ring-navy-500"
          />
          Show test agents
        </label>
        <label
          htmlFor="my-agents-show-suspended"
          className="inline-flex items-center gap-2 text-gray-600 cursor-pointer"
        >
          <input
            id="my-agents-show-suspended"
            type="checkbox"
            checked={showSuspended}
            onChange={(e) => setShowSuspended(e.target.checked)}
            className="rounded focus:ring-2 focus:ring-navy-500"
          />
          Show suspended
          {data == null ? (
            <span
              className="ml-1 inline-block h-3 w-6 rounded bg-gray-200 animate-pulse align-middle"
              aria-label="Loading suspended count"
            />
          ) : (
            ` (${suspendedCount})`
          )}
        </label>
      </div>

      {error && (
        <div className="bg-red-50 text-red-700 rounded-lg p-3 mb-4 flex items-center gap-2 text-sm">
          <AlertCircle className="w-4 h-4 flex-shrink-0" /> {error}
        </div>
      )}
      {actionError && (
        <div className="bg-red-50 text-red-700 rounded-lg p-3 mb-4 text-sm">
          {actionError}
        </div>
      )}

      {selected.size > 0 && (
        <div className="flex flex-wrap gap-2 mb-4 p-3 bg-gray-50 rounded-xl border border-gray-200">
          <span className="text-sm text-gray-600 self-center">
            {selected.size} selected
          </span>
          <button
            type="button"
            className="text-sm px-3 py-1.5 rounded-lg bg-red-50 text-red-700 border border-red-200 focus:outline-none focus:ring-2 focus:ring-navy-500"
            onClick={() =>
              setPending({
                kind: "bulk-suspend",
                count: filtered.filter(
                  (a) => selected.has(a.id) && a.status === "active"
                ).length,
              })
            }
          >
            Suspend selected
          </button>
          <button
            type="button"
            className="text-sm px-3 py-1.5 rounded-lg bg-green-50 text-green-700 border border-green-200 focus:outline-none focus:ring-2 focus:ring-navy-500"
            onClick={() =>
              setPending({
                kind: "bulk-unsuspend",
                count: filtered.filter(
                  (a) => selected.has(a.id) && a.status === "suspended"
                ).length,
              })
            }
          >
            Reactivate selected
          </button>
        </div>
      )}

      {data == null && !error ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-20 bg-gray-100 rounded-xl animate-pulse"
            />
          ))}
        </div>
      ) : data == null ? null : showOnboardingEmpty ? (
        <div className="text-center py-12 px-4 border border-dashed border-gray-300 rounded-xl">
          <p className="text-gray-600 mb-4 max-w-md mx-auto">
            An agent is your exchange identity for claiming bounties and settling payments.
          </p>
          <button
            type="button"
            onClick={() => setRegisterModalDevId(allDeveloperIds[0] ?? null)}
            className="inline-flex px-5 py-2.5 bg-money text-navy-900 rounded-lg text-sm font-semibold hover:bg-money-dark transition focus:outline-none focus:ring-2 focus:ring-navy-500"
          >
            Register agent
          </button>
          <p className="text-sm text-gray-500 mt-3">
            <Link href="/developers" className="text-navy-700 hover:underline">
              Developer docs
            </Link>
          </p>
        </div>
      ) : groups.size === 0 ? (
        <p className="text-gray-500 text-sm py-8 text-center">
          No agents match your filters.
        </p>
      ) : (
        <div className="space-y-6">
          {[...groups.entries()].map(([devId, agents]) => {
            const isCollapsed = collapsedGroups.has(devId);
            return (
              <section
                key={devId}
                className="border border-gray-200 rounded-xl bg-white overflow-hidden"
              >
                <button
                  type="button"
                  onClick={() => toggleGroupCollapsed(devId)}
                  aria-expanded={!isCollapsed}
                  className="w-full flex items-center justify-between gap-3 px-4 py-3 bg-gray-50 hover:bg-gray-100/80 transition text-left focus:outline-none focus:ring-2 focus:ring-inset focus:ring-navy-500"
                >
                  <span className="flex items-center gap-2 min-w-0">
                    {isCollapsed ? (
                      <ChevronRight
                        className="w-4 h-4 text-gray-500 flex-shrink-0"
                        aria-hidden="true"
                      />
                    ) : (
                      <ChevronDown
                        className="w-4 h-4 text-gray-500 flex-shrink-0"
                        aria-hidden="true"
                      />
                    )}
                    <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide flex-shrink-0">
                      Developer ID
                    </span>
                    <span className="font-mono text-xs font-semibold text-navy-900 truncate">
                      {devId}
                    </span>
                  </span>
                  {data == null ? (
                    <span
                      className="inline-block h-5 w-16 rounded-full bg-gray-200 animate-pulse flex-shrink-0"
                      aria-label="Loading agent count"
                    />
                  ) : (
                    <span className="text-xs font-medium text-gray-600 bg-white border border-gray-200 px-2.5 py-0.5 rounded-full flex-shrink-0">
                      {agents.length} {agents.length === 1 ? "agent" : "agents"}
                    </span>
                  )}
                </button>

                {!isCollapsed && (
                  <div className="p-3 sm:p-4">
                    {agents.length === 0 ? (
                      <EmptyDeveloperGroup
                        devId={devId}
                        hiddenSuspended={
                          showSuspended
                            ? 0
                            : scopedAgents.filter(
                                (a) =>
                                  (a.developer_id || "unknown") === devId &&
                                  a.status === "suspended"
                              ).length
                        }
                        onRegister={() => setRegisterModalDevId(devId)}
                      />
                    ) : (
                      <>
                        <AgentsTableDesktop
                          agents={agents}
                          selected={selected}
                          toggleSelected={toggleSelected}
                          acting={acting}
                          onPublishCard={(a) => setCardModalAgent(a)}
                          onSuspend={(a) =>
                            setPending({ kind: "suspend", agent: a })
                          }
                          onUnsuspend={(a) =>
                            setPending({ kind: "unsuspend", agent: a })
                          }
                          onRotate={(a) =>
                            setPending({ kind: "rotate", agent: a })
                          }
                        />
                        <AgentsListMobile
                          agents={agents}
                          renderRow={renderAgentRow}
                        />
                      </>
                    )}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      )}

      {pending && pending.kind !== "rotate" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40">
          <div
            className="bg-white rounded-xl max-w-md w-full p-6 border border-gray-200 shadow-xl"
            role="dialog"
            aria-modal="true"
          >
            <h3 className="font-semibold text-navy-900 mb-2">Confirm</h3>
            <p className="text-sm text-gray-600 mb-6">{confirmMessage()}</p>
            <div className="flex gap-3 justify-end">
              <button
                type="button"
                className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900 rounded-lg focus:outline-none focus:ring-2 focus:ring-navy-500"
                onClick={() => setPending(null)}
                disabled={acting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="px-4 py-2 text-sm bg-navy-900 text-white rounded-lg font-semibold disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-navy-500"
                onClick={() => void runConfirm()}
                disabled={acting}
              >
                {acting ? "Working…" : "Confirm"}
              </button>
            </div>
          </div>
        </div>
      )}

      {pending?.kind === "rotate" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40">
          <div
            className="bg-white rounded-xl max-w-md w-full p-6 border border-gray-200 shadow-xl"
            role="dialog"
            aria-modal="true"
          >
            <div className="flex items-center gap-2 mb-2">
              <Key className="w-4 h-4 text-money" />
              <h3 className="font-semibold text-navy-900">Rotate key</h3>
            </div>
            <p className="text-sm text-gray-600 mb-6">{confirmMessage()}</p>
            <div className="flex gap-3 justify-end">
              <button
                type="button"
                className="px-4 py-2 text-sm text-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-navy-500"
                onClick={() => setPending(null)}
                disabled={acting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="px-4 py-2 text-sm bg-navy-900 text-white rounded-lg font-semibold disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-navy-500"
                onClick={() => void runConfirm()}
                disabled={acting}
              >
                {acting ? "Rotating…" : "Rotate key"}
              </button>
            </div>
          </div>
        </div>
      )}

      {registerModalDevId !== undefined && (
        <RegisterAgentModal
          developerIds={allDeveloperIds}
          initialDeveloperId={registerModalDevId}
          onClose={() => setRegisterModalDevId(undefined)}
          onRegistered={(revealedKey) => {
            setRegisterModalDevId(undefined);
            setRotatedKey(revealedKey);
            void refresh();
          }}
        />
      )}

      {attachDevModalOpen && (
        <AddDeveloperIdModal
          onClose={() => setAttachDevModalOpen(false)}
          onAttached={() => {
            setAttachDevModalOpen(false);
            void refresh();
          }}
        />
      )}

      {cardModalAgent && (
        <PublishAgentCardModal
          agentId={cardModalAgent.id}
          agentName={cardModalAgent.bot_name}
          hasAgentCard={cardModalAgent.has_agent_card}
          initialCard={cardModalAgent.agent_card}
          initialDescription={cardModalAgent.description}
          initialSkills={cardModalAgent.skills}
          onClose={() => setCardModalAgent(null)}
          onPublished={() => {
            setCardModalAgent(null);
            void refresh();
          }}
        />
      )}

      {rotatedKey && (
        <RotatedKeyReveal
          rotatedKey={rotatedKey}
          onClose={() => setRotatedKey(null)}
        />
      )}
    </div>
  );
}

function EmptyDeveloperGroup({
  devId,
  hiddenSuspended,
  onRegister,
}: {
  devId: string;
  hiddenSuspended: number;
  onRegister: () => void;
}) {
  if (hiddenSuspended > 0) {
    return (
      <div className="text-center py-6 px-4 rounded-lg border border-dashed border-gray-200">
        <p className="text-xs text-gray-500">
          {hiddenSuspended} suspended{" "}
          {hiddenSuspended === 1 ? "agent is" : "agents are"} hidden. Turn on
          Show suspended to list {hiddenSuspended === 1 ? "it" : "them"}.
        </p>
      </div>
    );
  }
  return (
    <div className="text-center py-6 px-4 rounded-lg border border-dashed border-gray-200">
      <p className="text-xs text-gray-500 mb-2.5">
        No agents registered under{" "}
        <span className="font-mono text-gray-700">{devId}</span> yet.
      </p>
      <button
        type="button"
        onClick={onRegister}
        className="inline-flex items-center gap-1 text-xs font-semibold text-navy-800 hover:underline focus:outline-none focus:ring-2 focus:ring-navy-500 rounded px-1"
      >
        <Plus className="w-3.5 h-3.5" />
        Register agent under {devId}
      </button>
    </div>
  );
}

function AgentsListMobile({
  agents,
  renderRow,
}: {
  agents: ManagedAgent[];
  renderRow: (a: ManagedAgent) => ReactNode;
}) {
  return <div className="md:hidden space-y-3">{agents.map(renderRow)}</div>;
}

function AgentsTableDesktop({
  agents,
  selected,
  toggleSelected,
  acting,
  onPublishCard,
  onSuspend,
  onUnsuspend,
  onRotate,
}: {
  agents: ManagedAgent[];
  selected: Set<string>;
  toggleSelected: (id: string) => void;
  acting: boolean;
  onPublishCard: (a: ManagedAgent) => void;
  onSuspend: (a: ManagedAgent) => void;
  onUnsuspend: (a: ManagedAgent) => void;
  onRotate: (a: ManagedAgent) => void;
}) {
  return (
    <div className="hidden md:block overflow-x-auto border border-gray-200 rounded-xl">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-left text-gray-500">
          <tr>
            <th className="p-2.5 w-9" />
            <th className="p-2.5 font-medium">Agent</th>
            <th className="p-2.5 font-medium">Status</th>
            <th className="p-2.5 font-medium">Agent Card</th>
            <th className="p-2.5 font-medium">Reputation</th>
            <th className="p-2.5 font-medium">Last activity</th>
            <th className="p-2.5 w-11" />
          </tr>
        </thead>
        <tbody>
          {agents.map((agent) => (
            <tr key={agent.id} className="border-t border-gray-100">
              <td className="p-2.5 align-middle">
                <input
                  type="checkbox"
                  checked={selected.has(agent.id)}
                  onChange={() => toggleSelected(agent.id)}
                  aria-label={`Select ${agent.bot_name}`}
                  className="rounded focus:ring-2 focus:ring-navy-500"
                />
              </td>
              <td className="p-2.5 align-middle">
                <div className="flex items-center gap-2 flex-wrap">
                  <Link
                    href={`/agents/${agent.id}`}
                    className="font-semibold text-navy-900 hover:underline focus:outline-none focus:ring-2 focus:ring-navy-500 rounded"
                  >
                    {agent.bot_name}
                  </Link>
                  {agent.is_test && (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-800 border border-amber-200">
                      test
                    </span>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2 mt-1">
                  <IdCopy id={agent.id} />
                  <RunsBehindBadge agent={agent} />
                </div>
              </td>
              <td className="p-2.5 align-middle whitespace-nowrap">
                <StatusPill status={agent.status} />
              </td>
              <td className="p-2.5 align-middle whitespace-nowrap">
                <AgentCardPill hasCard={agent.has_agent_card} />
              </td>
              <td className="p-2.5 align-middle whitespace-nowrap">
                <ReputationScore score={agent.reputation ?? null} size="sm" />
              </td>
              <td className="p-2.5 align-middle whitespace-nowrap text-xs text-gray-600">
                {formatActivityDate(agent.last_activity_at ?? agent.created_at)}
              </td>
              <td className="p-2.5 align-middle text-right">
                <AgentRowMenu
                  agent={agent}
                  busy={acting}
                  onPublishCard={() => onPublishCard(agent)}
                  onSuspend={() => onSuspend(agent)}
                  onUnsuspend={() => onUnsuspend(agent)}
                  onRotate={() => onRotate(agent)}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
