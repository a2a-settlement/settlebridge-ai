import { useCallback } from "react";
import { Wallet, Lock, Unlock, AlertTriangle, DollarSign } from "lucide-react";
import MetricCard from "../../components/gateway/MetricCard";
import { fetchSettlementOverview, usePolling } from "../../services/gateway";

export default function SettlementOverview() {
  const { data, loading } = usePolling(
    useCallback(() => fetchSettlementOverview(), []),
    15000
  );

  return (
    <div className="max-w-7xl mx-auto px-4 py-8 space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Settlement Overview</h1>
        <p className="text-sm text-gray-500 mt-1">
          Escrow status, ATE token flows, and treasury fee accumulation
        </p>
      </div>

      {loading && !data ? (
        <p className="text-gray-400">Loading settlement data...</p>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
            <MetricCard
              label="Active Escrows"
              value={data?.active_escrows ?? 0}
              icon={<Lock className="w-4 h-4" />}
            />
            <MetricCard
              label="Total Locked"
              value={`${data?.total_locked ?? 0} ATE`}
              icon={<Wallet className="w-4 h-4" />}
            />
            <MetricCard
              label="Total Released"
              value={`${data?.total_released ?? 0} ATE`}
              icon={<Unlock className="w-4 h-4" />}
            />
            <MetricCard
              label="Total Disputed"
              value={`${data?.total_disputed ?? 0} ATE`}
              icon={<AlertTriangle className="w-4 h-4" />}
            />
            <MetricCard
              label="Treasury Fees"
              value={`${data?.treasury_fees ?? 0} ATE`}
              icon={<DollarSign className="w-4 h-4" />}
            />
          </div>

          {data?.top_agents && data.top_agents.length > 0 && (
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <h2 className="text-sm font-semibold text-gray-700 mb-4">Top Agents by Volume</h2>
              <div className="space-y-2">
                {data.top_agents.map((agent, i) => (
                  <div key={i} className="flex items-center justify-between text-sm">
                    <span className="text-gray-700">{String(agent.agent_id ?? agent.bot_id ?? `Agent ${i + 1}`)}</span>
                    <span className="font-medium tabular-nums text-gray-900">
                      {String(agent.volume ?? agent.amount ?? 0)} ATE
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
