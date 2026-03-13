import { useCallback, useState } from "react";
import PolicyDecisionBadge from "../../components/gateway/PolicyDecisionBadge";
import ExportButton from "../../components/gateway/ExportButton";
import { fetchAuditLog, exportAudit, usePolling } from "../../services/gateway";
import type { AuditEntry, PolicyDecision } from "../../types/gateway";

export default function AuditLog() {
  const [searchSource, setSearchSource] = useState("");
  const [decisionFilter, setDecisionFilter] = useState<PolicyDecision | "">("");
  const [page, setPage] = useState(1);

  const { data, loading } = usePolling(
    useCallback(
      () =>
        fetchAuditLog({
          source: searchSource || undefined,
          decision: decisionFilter || undefined,
          page,
          page_size: 50,
        }),
      [searchSource, decisionFilter, page]
    ),
    10000
  );

  return (
    <div className="max-w-7xl mx-auto px-4 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Audit Log</h1>
          <p className="text-sm text-gray-500 mt-1">
            Tamper-evident record of all gateway decisions
          </p>
        </div>
        <ExportButton onExport={exportAudit} filename="audit-log" />
      </div>

      <div className="flex items-center gap-4 flex-wrap">
        <input
          type="text"
          placeholder="Search by source agent..."
          value={searchSource}
          onChange={(e) => { setSearchSource(e.target.value); setPage(1); }}
          className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm w-64 focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
        />
        <select
          value={decisionFilter}
          onChange={(e) => { setDecisionFilter(e.target.value as PolicyDecision | ""); setPage(1); }}
          className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm"
        >
          <option value="">All decisions</option>
          <option value="approve">Approve</option>
          <option value="block">Block</option>
          <option value="flag">Flag</option>
        </select>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Time</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Hash</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Source</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Target</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Decision</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Escrow</th>
              <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">Status</th>
              <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Merkle</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading && !data ? (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-gray-400">Loading...</td>
              </tr>
            ) : data?.entries.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-gray-400">No audit entries</td>
              </tr>
            ) : (
              data?.entries.map((entry: AuditEntry) => (
                <tr key={entry.id} className="hover:bg-gray-50">
                  <td className="px-4 py-2 text-xs text-gray-400 tabular-nums whitespace-nowrap">
                    {new Date(entry.timestamp).toLocaleString()}
                  </td>
                  <td className="px-4 py-2 text-xs font-mono text-gray-500" title={entry.request_hash}>
                    {entry.request_hash.slice(0, 12)}...
                  </td>
                  <td className="px-4 py-2 text-sm text-gray-700">{entry.source_agent}</td>
                  <td className="px-4 py-2 text-sm text-gray-700">{entry.target_agent}</td>
                  <td className="px-4 py-2">
                    <PolicyDecisionBadge decision={entry.policy_decision} />
                  </td>
                  <td className="px-4 py-2 text-xs font-mono text-gray-500">
                    {entry.escrow_id ? entry.escrow_id.slice(0, 8) : "—"}
                  </td>
                  <td className="px-4 py-2 text-right text-sm tabular-nums text-gray-600">
                    {entry.response_status ?? "—"}
                  </td>
                  <td className="px-4 py-2 text-center">
                    {entry.merkle_root ? (
                      <span className="inline-block w-2 h-2 bg-green-500 rounded-full" title={`Root: ${entry.merkle_root}`} />
                    ) : (
                      <span className="inline-block w-2 h-2 bg-gray-300 rounded-full" title="No Merkle proof" />
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
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
          <span className="text-sm text-gray-500">
            Page {page} of {Math.ceil(data.total / 50)}
          </span>
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
