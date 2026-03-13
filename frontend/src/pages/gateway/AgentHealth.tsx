import { useCallback } from "react";
import { Link } from "react-router-dom";
import StatusBadge from "../../components/gateway/StatusBadge";
import ReputationGauge from "../../components/gateway/ReputationGauge";
import { fetchAgents, usePolling } from "../../services/gateway";
import type { AgentHealth as AgentHealthType } from "../../types/gateway";

export default function AgentHealth() {
  const { data: agents, loading } = usePolling(useCallback(() => fetchAgents(), []), 5000);

  return (
    <div className="max-w-7xl mx-auto px-4 py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Agent Health</h1>
        <p className="text-sm text-gray-500 mt-1">
          Monitor status, latency, and reputation across all registered agents
        </p>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Agent</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Reputation</th>
              <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">Latency</th>
              <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">Error Rate</th>
              <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">Requests</th>
              <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">Last Seen</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading && !agents ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-gray-400">Loading...</td>
              </tr>
            ) : agents?.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-gray-400">No agents registered</td>
              </tr>
            ) : (
              agents?.map((agent: AgentHealthType) => (
                <tr key={agent.agent_id} className="hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <Link
                      to={`/agents/${agent.agent_id}`}
                      className="text-sm font-medium text-blue-600 hover:text-blue-800"
                    >
                      {agent.agent_id}
                    </Link>
                    {agent.bot_id && (
                      <p className="text-xs text-gray-400">{agent.bot_id}</p>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={agent.status} />
                  </td>
                  <td className="px-4 py-3">
                    <ReputationGauge score={agent.reputation_score} />
                  </td>
                  <td className="px-4 py-3 text-right text-sm tabular-nums text-gray-600">
                    {agent.avg_latency_ms != null ? `${agent.avg_latency_ms.toFixed(0)}ms` : "—"}
                  </td>
                  <td className="px-4 py-3 text-right text-sm tabular-nums text-gray-600">
                    {agent.error_rate != null ? `${(agent.error_rate * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td className="px-4 py-3 text-right text-sm tabular-nums text-gray-600">
                    {agent.request_count}
                  </td>
                  <td className="px-4 py-3 text-right text-xs text-gray-400">
                    {agent.last_seen
                      ? new Date(agent.last_seen).toLocaleTimeString()
                      : "Never"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
