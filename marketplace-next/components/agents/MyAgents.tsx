"use client";

import { useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import {
  AlertCircle,
  Bot,
  Check,
  Copy,
  Key,
  MoreVertical,
  Plus,
  RefreshCw,
  Search,
} from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import { groupAgentsByDeveloperId, useManagedAgents } from "@/hooks/useManagedAgents";
import type { ManagedAgent, RotatedKeyResult } from "@/lib/managedAgents";
import { truncateAgentId } from "@/lib/managedAgents";
import ReputationScore from "@/components/ReputationScore";
import RotatedKeyReveal from "@/components/agents/RotatedKeyReveal";
import api from "@/services/api";

type PendingAction =
  | { kind: "suspend"; agent: ManagedAgent }
  | { kind: "unsuspend"; agent: ManagedAgent }
  | { kind: "rotate"; agent: ManagedAgent }
  | { kind: "bulk-suspend"; count: number }
  | { kind: "bulk-unsuspend"; count: number };

function StatusPill({ status }: { status: string }) {
  const active = status === "active";
  return (
    <span
      className={`text-xs px-2 py-0.5 rounded-full font-medium capitalize ${
        active ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-600"
      }`}
    >
      {status || "unknown"}
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
        className="text-gray-400 hover:text-navy-700"
        onClick={() => {
          void navigator.clipboard.writeText(id);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? <Check className="w-3 h-3 text-money" /> : <Copy className="w-3 h-3" />}
      </button>
    </span>
  );
}

function AgentRowMenu({
  agent,
  busy,
  onSuspend,
  onUnsuspend,
  onRotate,
}: {
  agent: ManagedAgent;
  busy: boolean;
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
        className="p-1.5 rounded-lg text-gray-500 hover:bg-gray-100 disabled:opacity-50"
        aria-label="Actions"
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
          <div className="absolute right-0 z-20 mt-1 w-44 bg-white border border-gray-200 rounded-lg shadow-lg py-1 text-sm">
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
          </div>
        </>
      )}
    </div>
  );
}

export default function MyAgents() {
  const { user } = useAuth();
  const linked = !!user?.exchange_bot_id;
  const { bots, loading, error, refresh } = useManagedAgents(linked);

  const [search, setSearch] = useState("");
  const [showSuspended, setShowSuspended] = useState(false);
  const [showTest, setShowTest] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [acting, setActing] = useState(false);
  const [actionError, setActionError] = useState("");
  const [rotatedKey, setRotatedKey] = useState<RotatedKeyResult | null>(null);

  const listAgents = useMemo(
    () => bots.filter((b) => !b.own && b.id !== user?.exchange_bot_id),
    [bots, user?.exchange_bot_id]
  );

  const suspendedCount = useMemo(
    () => listAgents.filter((a) => a.status === "suspended").length,
    [listAgents]
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return listAgents.filter((a) => {
      if (!showSuspended && a.status === "suspended") return false;
      if (!showTest && a.is_test) return false;
      if (!q) return true;
      return (
        a.bot_name?.toLowerCase().includes(q) ||
        a.id.toLowerCase().includes(q)
      );
    });
  }, [listAgents, search, showSuspended, showTest]);

  const groups = useMemo(
    () => groupAgentsByDeveloperId(filtered),
    [filtered]
  );

  if (!user) {
    return (
      <div className="text-center py-16 px-4">
        <Bot className="w-12 h-12 mx-auto mb-4 text-gray-300" />
        <p className="text-gray-600 mb-4">Sign in to manage your settlement agents.</p>
        <Link
          href="/login?next=/agents%3Ftab%3Dmine"
          className="inline-flex px-5 py-2.5 bg-money text-navy-900 rounded-lg text-sm font-semibold hover:bg-money-dark transition"
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
          className="inline-flex px-5 py-2.5 bg-navy-900 text-white rounded-lg text-sm font-semibold hover:bg-navy-800 transition"
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
          `/bots/${pending.agent.id}/rotate-key`
        );
        setRotatedKey(res.data);
        setPending(null);
        await refresh();
        return;
      }
      if (pending.kind === "suspend") {
        await api.post(`/bots/${pending.agent.id}/suspend`);
      } else if (pending.kind === "unsuspend") {
        await api.post(`/bots/${pending.agent.id}/unsuspend`);
      } else if (pending.kind === "bulk-suspend") {
        const targets = filtered.filter(
          (a) => selected.has(a.id) && a.status === "active"
        );
        await Promise.all(
          targets.map((a) => api.post(`/bots/${a.id}/suspend`))
        );
        setSelected(new Set());
      } else if (pending.kind === "bulk-unsuspend") {
        const targets = filtered.filter(
          (a) => selected.has(a.id) && a.status === "suspended"
        );
        await Promise.all(
          targets.map((a) => api.post(`/bots/${a.id}/unsuspend`))
        );
        setSelected(new Set());
      }
      setPending(null);
      await refresh();
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || "Action failed";
      setActionError(typeof detail === "string" ? detail : "Action failed");
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
    return (
      <div
        key={agent.id}
        className="border border-gray-200 rounded-xl p-4 flex flex-col sm:flex-row sm:items-center gap-3"
      >
        <input
          type="checkbox"
          checked={checked}
          onChange={() => toggleSelected(agent.id)}
          className="flex-shrink-0 md:hidden"
          aria-label={`Select ${agent.bot_name}`}
        />
        <div className="flex-1 min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href={`/agents/${agent.id}`}
              className="font-semibold text-navy-900 hover:text-navy-700 text-sm"
            >
              {agent.bot_name}
            </Link>
            <StatusPill status={agent.status} />
            {agent.is_test && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-800 border border-amber-200">
                test
              </span>
            )}
          </div>
          <IdCopy id={agent.id} />
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          {agent.reputation != null && (
            <ReputationScore score={agent.reputation} size="sm" />
          )}
          <AgentRowMenu
            agent={agent}
            busy={acting}
            onSuspend={() => setPending({ kind: "suspend", agent })}
            onUnsuspend={() => setPending({ kind: "unsuspend", agent })}
            onRotate={() => setPending({ kind: "rotate", agent })}
          />
        </div>
      </div>
    );
  };

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
          <Link
            href="/developers"
            className="inline-flex items-center gap-1.5 px-4 py-2 bg-money text-navy-900 rounded-lg text-sm font-semibold hover:bg-money-dark transition"
          >
            <Plus className="w-4 h-4" />
            Register agent
          </Link>
          <Link
            href="/dashboard/settings"
            className="inline-flex items-center gap-1.5 px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-navy-800 hover:bg-gray-50 transition"
          >
            Add Developer ID
          </Link>
        </div>
      </div>

      {user.exchange_bot_id && (
        <div className="bg-navy-900 text-white rounded-xl p-4 mb-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div>
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">Your account</p>
            <p className="font-semibold">Linked exchange identity</p>
            <p className="font-mono text-xs text-gray-300 mt-1" title={user.exchange_bot_id}>
              {truncateAgentId(user.exchange_bot_id)}
            </p>
          </div>
          <Link
            href="/dashboard/settings"
            className="text-sm font-medium text-money hover:text-money-dark"
          >
            Settings →
          </Link>
        </div>
      )}

      <div className="flex flex-col md:flex-row md:items-center gap-3 mb-4">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="search"
            placeholder="Search by name or ID…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-10 pr-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm"
          />
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="inline-flex items-center gap-1.5 text-sm text-navy-700 font-medium"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      <div className="flex flex-wrap gap-4 text-sm mb-4">
        {suspendedCount > 0 && (
          <label className="inline-flex items-center gap-2 text-gray-600 cursor-pointer">
            <input
              type="checkbox"
              checked={showSuspended}
              onChange={(e) => setShowSuspended(e.target.checked)}
            />
            Show suspended ({suspendedCount})
          </label>
        )}
        <label className="inline-flex items-center gap-2 text-gray-600 cursor-pointer">
          <input
            type="checkbox"
            checked={showTest}
            onChange={(e) => setShowTest(e.target.checked)}
          />
          Show test agents
        </label>
      </div>

      {error && (
        <div className="bg-red-50 text-red-700 rounded-lg p-3 mb-4 flex items-center gap-2 text-sm">
          <AlertCircle className="w-4 h-4 flex-shrink-0" /> {error}
        </div>
      )}
      {actionError && (
        <div className="bg-red-50 text-red-700 rounded-lg p-3 mb-4 text-sm">{actionError}</div>
      )}

      {selected.size > 0 && (
        <div className="flex flex-wrap gap-2 mb-4 p-3 bg-gray-50 rounded-xl border border-gray-200">
          <span className="text-sm text-gray-600 self-center">{selected.size} selected</span>
          <button
            type="button"
            className="text-sm px-3 py-1.5 rounded-lg bg-red-50 text-red-700 border border-red-200"
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
            className="text-sm px-3 py-1.5 rounded-lg bg-green-50 text-green-700 border border-green-200"
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

      {loading && listAgents.length === 0 ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-20 bg-gray-100 rounded-xl animate-pulse" />
          ))}
        </div>
      ) : filtered.length === 0 && listAgents.length === 0 ? (
        <div className="text-center py-12 px-4 border border-dashed border-gray-300 rounded-xl">
          <p className="text-gray-600 mb-4 max-w-md mx-auto">
            An agent is your exchange identity for claiming bounties and settling payments.
          </p>
          <Link
            href="/developers"
            className="inline-flex px-5 py-2.5 bg-money text-navy-900 rounded-lg text-sm font-semibold hover:bg-money-dark transition"
          >
            Register agent
          </Link>
          <p className="text-sm text-gray-500 mt-3">
            <Link href="/developers" className="text-navy-700 hover:underline">
              Developer docs
            </Link>
          </p>
        </div>
      ) : filtered.length === 0 ? (
        <p className="text-gray-500 text-sm py-8 text-center">No agents match your filters.</p>
      ) : (
        <div className="space-y-8">
          {[...groups.entries()].map(([devId, agents]) => (
            <section key={devId}>
              <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
                Developer ID · {devId}
              </h3>
              <AgentsTableDesktop
                agents={agents}
                selected={selected}
                toggleSelected={toggleSelected}
                acting={acting}
                onSuspend={(a) => setPending({ kind: "suspend", agent: a })}
                onUnsuspend={(a) => setPending({ kind: "unsuspend", agent: a })}
                onRotate={(a) => setPending({ kind: "rotate", agent: a })}
              />
              <AgentsListMobile agents={agents} renderRow={renderAgentRow} />
            </section>
          ))}
        </div>
      )}

      {pending && pending.kind !== "rotate" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40">
          <div className="bg-white rounded-xl max-w-md w-full p-6 border border-gray-200 shadow-xl">
            <h3 className="font-semibold text-navy-900 mb-2">Confirm</h3>
            <p className="text-sm text-gray-600 mb-6">{confirmMessage()}</p>
            <div className="flex gap-3 justify-end">
              <button
                type="button"
                className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900"
                onClick={() => setPending(null)}
                disabled={acting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="px-4 py-2 text-sm bg-navy-900 text-white rounded-lg font-semibold disabled:opacity-50"
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
          <div className="bg-white rounded-xl max-w-md w-full p-6 border border-gray-200 shadow-xl">
            <div className="flex items-center gap-2 mb-2">
              <Key className="w-4 h-4 text-money" />
              <h3 className="font-semibold text-navy-900">Rotate key</h3>
            </div>
            <p className="text-sm text-gray-600 mb-6">{confirmMessage()}</p>
            <div className="flex gap-3 justify-end">
              <button
                type="button"
                className="px-4 py-2 text-sm text-gray-600"
                onClick={() => setPending(null)}
                disabled={acting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="px-4 py-2 text-sm bg-navy-900 text-white rounded-lg font-semibold disabled:opacity-50"
                onClick={() => void runConfirm()}
                disabled={acting}
              >
                {acting ? "Rotating…" : "Rotate key"}
              </button>
            </div>
          </div>
        </div>
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
  onSuspend,
  onUnsuspend,
  onRotate,
}: {
  agents: ManagedAgent[];
  selected: Set<string>;
  toggleSelected: (id: string) => void;
  acting: boolean;
  onSuspend: (a: ManagedAgent) => void;
  onUnsuspend: (a: ManagedAgent) => void;
  onRotate: (a: ManagedAgent) => void;
}) {
  return (
    <div className="hidden md:block overflow-x-auto border border-gray-200 rounded-xl">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-left text-gray-500">
          <tr>
            <th className="p-3 w-10" />
            <th className="p-3 font-medium">Name</th>
            <th className="p-3 font-medium">Status</th>
            <th className="p-3 font-medium">Reputation</th>
            <th className="p-3 font-medium">ID</th>
            <th className="p-3 w-12" />
          </tr>
        </thead>
        <tbody>
          {agents.map((agent) => (
            <tr key={agent.id} className="border-t border-gray-100">
              <td className="p-3">
                <input
                  type="checkbox"
                  checked={selected.has(agent.id)}
                  onChange={() => toggleSelected(agent.id)}
                  aria-label={`Select ${agent.bot_name}`}
                />
              </td>
              <td className="p-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <Link
                    href={`/agents/${agent.id}`}
                    className="font-semibold text-navy-900 hover:underline"
                  >
                    {agent.bot_name}
                  </Link>
                  {agent.is_test && (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-800">
                      test
                    </span>
                  )}
                </div>
              </td>
              <td className="p-3">
                <StatusPill status={agent.status} />
              </td>
              <td className="p-3">
                <ReputationScore score={agent.reputation ?? null} size="sm" />
              </td>
              <td className="p-3">
                <IdCopy id={agent.id} />
              </td>
              <td className="p-3">
                <AgentRowMenu
                  agent={agent}
                  busy={acting}
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