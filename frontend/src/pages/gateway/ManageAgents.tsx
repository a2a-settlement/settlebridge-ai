import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import {
  claimAgent,
  fetchClaimedAgents,
  fetchGatewayHealth,
  searchExchangeDirectory,
  unclaimAgent,
  usePolling,
} from "../../services/gateway";
import type { ExchangeAgentResult, GatewayAgentClaim } from "../../types/gateway";

export default function ManageAgents() {
  const {
    data: claimed,
    loading,
    refresh,
  } = usePolling(useCallback(() => fetchClaimedAgents(), []), 15000);
  const { data: health } = usePolling(useCallback(() => fetchGatewayHealth(), []), 30000);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ExchangeAgentResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSearch(e: React.FormEvent) {
    e.preventDefault();
    setSearching(true);
    setError(null);
    try {
      const rows = await searchExchangeDirectory(query.trim() || undefined);
      setResults(rows);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setSearching(false);
    }
  }

  async function onClaim(id: string) {
    setBusyId(id);
    setError(null);
    try {
      await claimAgent(id);
      await refresh();
      setResults((prev) =>
        prev
          ? prev.map((r) => (r.id === id ? { ...r, already_claimed: true } : r))
          : prev
      );
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (err instanceof Error ? err.message : "Claim failed");
      setError(String(detail));
    } finally {
      setBusyId(null);
    }
  }

  async function onUnclaim(id: string) {
    if (!confirm("Release this agent from the gateway health monitor?")) return;
    setBusyId(id);
    setError(null);
    try {
      await unclaimAgent(id);
      await refresh();
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (err instanceof Error ? err.message : "Unclaim failed");
      setError(String(detail));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-8 space-y-8">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Manage Agents</h1>
          <p className="text-sm text-gray-500 mt-1">
            Claim exchange bots into this gateway for health monitoring and policy
          </p>
        </div>
        <Link
          to="/agents"
          className="text-sm text-blue-600 hover:text-blue-800 font-medium"
        >
          ← Agent Health
        </Link>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {health && !health.can_claim_on_exchange && (
        <div className="rounded-lg border border-yellow-200 bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
          <p className="font-medium">Claims are recorded locally only</p>
          <p className="mt-0.5 text-yellow-700">
            This gateway authenticates to the exchange as an account of type{" "}
            <span className="font-mono">{health.exchange_account_type ?? "unknown"}</span>.
            The exchange only accepts claims from <span className="font-mono">gateway</span>{" "}
            accounts, so agents stay key-unverified and no claim is recorded on the
            exchange. Monitoring and policy still work.
          </p>
        </div>
      )}

      <section className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-100">
          <h2 className="text-sm font-semibold text-gray-800">Claimed on this gateway</h2>
        </div>
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Bot</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Account</th>
              <th
                className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase"
                title="Whether the agent's own API key was presented at claim time, proving key ownership. Unrelated to health or attestation."
              >
                Key-verified
              </th>
              <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading && !claimed ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-gray-400">
                  Loading...
                </td>
              </tr>
            ) : !claimed?.length ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-gray-400">
                  No claimed agents yet — search the directory below
                </td>
              </tr>
            ) : (
              claimed.map((a: GatewayAgentClaim) => (
                <tr key={a.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 text-sm font-medium text-gray-900">
                    {a.bot_name}
                  </td>
                  <td className="px-4 py-3 text-xs font-mono text-gray-500">
                    {a.exchange_account_id}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-600">
                    {a.verified ? "Yes" : "No"}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      type="button"
                      disabled={busyId === a.exchange_account_id}
                      onClick={() => onUnclaim(a.exchange_account_id)}
                      className="text-sm text-red-600 hover:text-red-800 disabled:opacity-50"
                    >
                      Release
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        <p className="px-4 py-3 text-xs text-gray-400 border-t border-gray-100">
          Key-verified means the agent's own API key was presented when claiming, proving
          key ownership. A soft claim is still enough for health monitoring and policy.
        </p>
      </section>

      <section className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
        <h2 className="text-sm font-semibold text-gray-800">Claim from exchange directory</h2>
        <form onSubmit={onSearch} className="flex flex-col sm:flex-row gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search bot name or developer…"
            className="flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            type="submit"
            disabled={searching}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            {searching ? "Searching…" : "Search"}
          </button>
        </form>

        {results && (
          <div className="border border-gray-100 rounded-lg overflow-hidden">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                    Bot
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                    Reputation
                  </th>
                  <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">
                    Action
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {results.length === 0 ? (
                  <tr>
                    <td colSpan={3} className="px-4 py-6 text-center text-gray-400 text-sm">
                      No matches
                    </td>
                  </tr>
                ) : (
                  results.map((r) => (
                    <tr key={r.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3">
                        <p className="text-sm font-medium text-gray-900">{r.bot_name}</p>
                        <p className="text-xs text-gray-400 font-mono">{r.id}</p>
                      </td>
                      <td className="px-4 py-3 text-sm tabular-nums text-gray-600">
                        {r.reputation?.toFixed?.(2) ?? r.reputation}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {r.already_claimed ? (
                          <span className="text-xs text-gray-400">Claimed</span>
                        ) : (
                          <button
                            type="button"
                            disabled={busyId === r.id}
                            onClick={() => onClaim(r.id)}
                            className="text-sm text-blue-600 hover:text-blue-800 disabled:opacity-50"
                          >
                            Claim
                          </button>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
