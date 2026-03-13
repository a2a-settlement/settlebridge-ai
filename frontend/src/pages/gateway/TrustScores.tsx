import { useCallback } from "react";
import ReputationGauge from "../../components/gateway/ReputationGauge";
import StatusBadge from "../../components/gateway/StatusBadge";
import { fetchAgents, usePolling } from "../../services/gateway";
import type { AgentHealth } from "../../types/gateway";

export default function TrustScores() {
  const { data: agents, loading } = usePolling(useCallback(() => fetchAgents(), []), 10000);

  const sorted = [...(agents ?? [])].sort(
    (a, b) => (b.reputation_score ?? 0) - (a.reputation_score ?? 0)
  );

  const distribution = { high: 0, medium: 0, low: 0 };
  for (const a of sorted) {
    const s = a.reputation_score ?? 0;
    if (s >= 0.7) distribution.high++;
    else if (s >= 0.4) distribution.medium++;
    else distribution.low++;
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-8 space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Trust Scores</h1>
        <p className="text-sm text-gray-500 mt-1">
          Reputation scores and trends for all registered agents
        </p>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <div className="bg-green-50 rounded-xl p-4 text-center">
          <div className="text-2xl font-bold text-green-700">{distribution.high}</div>
          <div className="text-xs text-green-600 mt-1">High (0.7+)</div>
        </div>
        <div className="bg-yellow-50 rounded-xl p-4 text-center">
          <div className="text-2xl font-bold text-yellow-700">{distribution.medium}</div>
          <div className="text-xs text-yellow-600 mt-1">Medium (0.4–0.7)</div>
        </div>
        <div className="bg-red-50 rounded-xl p-4 text-center">
          <div className="text-2xl font-bold text-red-700">{distribution.low}</div>
          <div className="text-xs text-red-600 mt-1">Low (&lt;0.4)</div>
        </div>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Agent</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Reputation</th>
              <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">Requests</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading && !agents ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-gray-400">Loading...</td>
              </tr>
            ) : sorted.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-gray-400">No agents</td>
              </tr>
            ) : (
              sorted.map((agent: AgentHealth) => (
                <tr key={agent.agent_id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 text-sm font-medium text-gray-900">{agent.agent_id}</td>
                  <td className="px-4 py-3"><StatusBadge status={agent.status} /></td>
                  <td className="px-4 py-3"><ReputationGauge score={agent.reputation_score} /></td>
                  <td className="px-4 py-3 text-right text-sm tabular-nums text-gray-600">{agent.request_count}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
