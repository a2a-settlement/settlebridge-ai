import { useCallback, useState } from "react";
import PolicyDecisionBadge from "../../components/gateway/PolicyDecisionBadge";
import LiveFeed from "../../components/gateway/LiveFeed";
import { fetchTransactions, usePolling } from "../../services/gateway";
import type { AuditEntry, PolicyDecision } from "../../types/gateway";

export default function TransactionFlow() {
  const [sourceFilter, setSourceFilter] = useState("");
  const [decisionFilter, setDecisionFilter] = useState<PolicyDecision | "">("");
  const [page, setPage] = useState(1);

  const { data, loading } = usePolling(
    useCallback(
      () =>
        fetchTransactions({
          source: sourceFilter || undefined,
          decision: decisionFilter || undefined,
          page,
          page_size: 50,
        }),
      [sourceFilter, decisionFilter, page]
    ),
    3000
  );

  return (
    <div className="max-w-7xl mx-auto px-4 py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Transaction Flow</h1>
        <p className="text-sm text-gray-500 mt-1">Live feed of gateway traffic</p>
      </div>

      <div className="flex items-center gap-4 flex-wrap">
        <input
          type="text"
          placeholder="Filter by source agent..."
          value={sourceFilter}
          onChange={(e) => { setSourceFilter(e.target.value); setPage(1); }}
          className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm w-64 focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
        />
        <select
          value={decisionFilter}
          onChange={(e) => { setDecisionFilter(e.target.value as PolicyDecision | ""); setPage(1); }}
          className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500"
        >
          <option value="">All decisions</option>
          <option value="approve">Approve</option>
          <option value="block">Block</option>
          <option value="flag">Flag</option>
        </select>
        <span className="text-xs text-gray-400">
          {data ? `${data.total} total` : "Loading..."}
        </span>
      </div>

      <div className="bg-white rounded-xl border border-gray-200">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Time</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Source</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Target</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Decision</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">Latency</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {loading && !data ? (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-gray-400">Loading...</td>
                </tr>
              ) : (
                data?.entries.map((entry: AuditEntry) => (
                  <tr key={entry.id} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-xs text-gray-400 tabular-nums">
                      {new Date(entry.timestamp).toLocaleTimeString()}
                    </td>
                    <td className="px-4 py-2 text-sm text-gray-700">{entry.source_agent}</td>
                    <td className="px-4 py-2 text-sm text-gray-700">{entry.target_agent}</td>
                    <td className="px-4 py-2">
                      <PolicyDecisionBadge decision={entry.policy_decision} />
                    </td>
                    <td className="px-4 py-2 text-right text-sm tabular-nums text-gray-600">
                      {entry.latency_ms ? `${entry.latency_ms}ms` : "—"}
                    </td>
                    <td className="px-4 py-2 text-right text-sm tabular-nums text-gray-600">
                      {entry.response_status ?? "—"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {data && data.total > 50 && (
        <div className="flex items-center justify-center gap-4">
          <button
            onClick={() => setPage(Math.max(1, page - 1))}
            disabled={page === 1}
            className="px-3 py-1 text-sm border rounded-lg disabled:opacity-50"
          >
            Previous
          </button>
          <span className="text-sm text-gray-500">Page {page}</span>
          <button
            onClick={() => setPage(page + 1)}
            disabled={page * 50 >= data.total}
            className="px-3 py-1 text-sm border rounded-lg disabled:opacity-50"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
