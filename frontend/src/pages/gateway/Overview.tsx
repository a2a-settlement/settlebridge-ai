import { useCallback } from "react";
import { Activity, Shield, Users, Zap, AlertTriangle, Clock } from "lucide-react";
import MetricCard from "../../components/gateway/MetricCard";
import PolicyDecisionBadge from "../../components/gateway/PolicyDecisionBadge";
import LiveFeed from "../../components/gateway/LiveFeed";
import {
  fetchGatewayHealth,
  fetchTransactions,
  fetchMetrics,
  usePolling,
} from "../../services/gateway";
import type { AuditEntry } from "../../types/gateway";

export default function Overview() {
  const { data: health } = usePolling(useCallback(() => fetchGatewayHealth(), []), 5000);
  const { data: metrics } = usePolling(useCallback(() => fetchMetrics(), []), 10000);
  const { data: txData } = usePolling(
    useCallback(() => fetchTransactions({ page_size: 20 }), []),
    5000
  );

  return (
    <div className="max-w-7xl mx-auto px-4 py-8 space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Gateway Overview</h1>
        <p className="text-sm text-gray-500 mt-1">
          Real-time status of your SettleBridge Gateway
        </p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4">
        <MetricCard
          label="Active Agents"
          value={health?.active_agents ?? "—"}
          icon={<Users className="w-4 h-4" />}
        />
        <MetricCard
          label="Transactions"
          value={health?.total_transactions ?? "—"}
          icon={<Activity className="w-4 h-4" />}
        />
        <MetricCard
          label="Policy Violations"
          value={health?.policy_violations_24h ?? "—"}
          icon={<Shield className="w-4 h-4" />}
        />
        <MetricCard
          label="Avg Latency"
          value={health ? `${health.avg_latency_ms}ms` : "—"}
          icon={<Clock className="w-4 h-4" />}
        />
        <MetricCard
          label="Error Rate"
          value={metrics ? `${(metrics.error_rate * 100).toFixed(1)}%` : "—"}
          icon={<AlertTriangle className="w-4 h-4" />}
        />
        <MetricCard
          label="Cache Hit Rate"
          value={metrics ? `${(metrics.cache_hit_rate * 100).toFixed(0)}%` : "—"}
          icon={<Zap className="w-4 h-4" />}
        />
      </div>

      {health && !health.exchange_connected && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4 flex items-center gap-3">
          <AlertTriangle className="w-5 h-5 text-yellow-600 flex-shrink-0" />
          <div>
            <p className="text-sm font-medium text-yellow-800">Exchange disconnected</p>
            <p className="text-xs text-yellow-600">
              Gateway is serving cached data. Reconnection will be attempted automatically.
            </p>
          </div>
        </div>
      )}

      <div className="grid lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">
            Decisions Breakdown
          </h2>
          {metrics?.requests_by_decision ? (
            <div className="space-y-3">
              {Object.entries(metrics.requests_by_decision).map(([decision, count]) => (
                <div key={decision} className="flex items-center justify-between">
                  <PolicyDecisionBadge decision={decision as "approve" | "block" | "flag"} />
                  <span className="text-sm font-medium text-gray-900 tabular-nums">{count}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-gray-400 text-sm">Loading...</p>
          )}
        </div>

        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">
            Recent Activity
          </h2>
          <LiveFeed
            items={txData?.entries ?? []}
            renderItem={(entry: AuditEntry) => (
              <div className="flex items-center justify-between text-sm">
                <div className="flex items-center gap-2 min-w-0">
                  <PolicyDecisionBadge decision={entry.policy_decision} />
                  <span className="text-gray-600 truncate">
                    {entry.source_agent} → {entry.target_agent}
                  </span>
                </div>
                <span className="text-xs text-gray-400 tabular-nums flex-shrink-0">
                  {entry.latency_ms ? `${entry.latency_ms}ms` : ""}
                </span>
              </div>
            )}
          />
        </div>
      </div>
    </div>
  );
}
