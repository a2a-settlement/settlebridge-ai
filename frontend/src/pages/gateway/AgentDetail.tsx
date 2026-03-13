import { useParams } from "react-router-dom";
import { useCallback } from "react";
import StatusBadge from "../../components/gateway/StatusBadge";
import ReputationGauge from "../../components/gateway/ReputationGauge";
import PolicyDecisionBadge from "../../components/gateway/PolicyDecisionBadge";
import MetricCard from "../../components/gateway/MetricCard";
import { fetchAgentDetail, usePolling } from "../../services/gateway";
import type { AgentStatus } from "../../types/gateway";

export default function AgentDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: agent, loading } = usePolling(
    useCallback(() => fetchAgentDetail(id!), [id]),
    5000
  );

  if (loading && !agent) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-8">
        <p className="text-gray-400">Loading agent details...</p>
      </div>
    );
  }

  if (!agent) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-8">
        <p className="text-gray-500">Agent not found</p>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-8 space-y-8">
      <div className="flex items-center gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{agent.agent_id}</h1>
          {agent.bot_id && <p className="text-sm text-gray-500">{agent.bot_id}</p>}
        </div>
        <StatusBadge status={agent.status as AgentStatus} />
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          label="Reputation"
          value={agent.reputation_score?.toFixed(2) ?? "N/A"}
        />
        <MetricCard
          label="Avg Latency"
          value={agent.avg_latency_ms != null ? `${agent.avg_latency_ms.toFixed(0)}ms` : "N/A"}
        />
        <MetricCard
          label="Error Rate"
          value={agent.error_rate != null ? `${(agent.error_rate * 100).toFixed(1)}%` : "N/A"}
        />
        <MetricCard
          label="Total Requests"
          value={agent.request_count}
        />
      </div>

      <div className="grid lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">Reputation History</h2>
          {agent.reputation_history.length === 0 ? (
            <p className="text-gray-400 text-sm">No history yet</p>
          ) : (
            <div className="space-y-2">
              {agent.reputation_history.slice(0, 20).map((snap) => (
                <div key={snap.id} className="flex items-center justify-between">
                  <span className="text-xs text-gray-400">
                    {new Date(snap.snapshot_at).toLocaleDateString()}
                  </span>
                  <ReputationGauge score={snap.reputation_score} showBar={false} />
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">Recent Transactions</h2>
          {agent.recent_transactions.length === 0 ? (
            <p className="text-gray-400 text-sm">No transactions yet</p>
          ) : (
            <div className="space-y-2">
              {agent.recent_transactions.map((tx) => (
                <div key={tx.id} className="flex items-center justify-between text-sm">
                  <div className="flex items-center gap-2">
                    <PolicyDecisionBadge decision={tx.policy_decision} />
                    <span className="text-gray-600 truncate">
                      → {tx.target_agent}
                    </span>
                  </div>
                  <span className="text-xs text-gray-400">
                    {tx.latency_ms ? `${tx.latency_ms}ms` : ""}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
