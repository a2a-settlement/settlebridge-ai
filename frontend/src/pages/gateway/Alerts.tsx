import { useCallback, useState } from "react";
import { Plus, Bell, AlertTriangle } from "lucide-react";
import {
  fetchAlerts,
  createAlertRule,
  updateAlertRule,
  usePolling,
} from "../../services/gateway";
import type { AlertChannel, AlertConditionType, AlertEvent, AlertRule } from "../../types/gateway";

const CONDITION_LABELS: Record<AlertConditionType, string> = {
  reputation_below: "Reputation Below",
  spending_approaching: "Spending Approaching",
  error_rate_above: "Error Rate Above",
  anomalous_volume: "Anomalous Volume",
  policy_violation_spike: "Policy Violation Spike",
};

export default function Alerts() {
  const { data, refresh } = usePolling(useCallback(() => fetchAlerts(), []), 10000);
  const [showCreate, setShowCreate] = useState(false);
  const [newRule, setNewRule] = useState({
    name: "",
    condition_type: "reputation_below" as AlertConditionType,
    threshold: 0.3,
    channel: "dashboard" as AlertChannel,
    agent_filter: "",
  });

  const handleCreate = async () => {
    if (!newRule.name.trim()) return;
    await createAlertRule({
      name: newRule.name,
      condition_type: newRule.condition_type,
      threshold: newRule.threshold,
      channel: newRule.channel,
      agent_filter: newRule.agent_filter || null,
    });
    setShowCreate(false);
    setNewRule({ name: "", condition_type: "reputation_below", threshold: 0.3, channel: "dashboard", agent_filter: "" });
    refresh();
  };

  const handleToggle = async (rule: AlertRule) => {
    await updateAlertRule(rule.id, { active: !rule.active });
    refresh();
  };

  return (
    <div className="max-w-7xl mx-auto px-4 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Alerts</h1>
          <p className="text-sm text-gray-500 mt-1">
            Active alerts and rule configuration
          </p>
        </div>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className="inline-flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition"
        >
          <Plus className="w-4 h-4" /> New Rule
        </button>
      </div>

      {showCreate && (
        <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <input
              type="text"
              placeholder="Rule name"
              value={newRule.name}
              onChange={(e) => setNewRule({ ...newRule, name: e.target.value })}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
            />
            <select
              value={newRule.condition_type}
              onChange={(e) => setNewRule({ ...newRule, condition_type: e.target.value as AlertConditionType })}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
            >
              {Object.entries(CONDITION_LABELS).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
            <input
              type="number"
              step="0.01"
              placeholder="Threshold"
              value={newRule.threshold}
              onChange={(e) => setNewRule({ ...newRule, threshold: parseFloat(e.target.value) })}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
            />
            <select
              value={newRule.channel}
              onChange={(e) => setNewRule({ ...newRule, channel: e.target.value as AlertChannel })}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
            >
              <option value="dashboard">Dashboard</option>
              <option value="webhook">Webhook</option>
              <option value="email">Email</option>
            </select>
          </div>
          <input
            type="text"
            placeholder="Agent filter (optional)"
            value={newRule.agent_filter}
            onChange={(e) => setNewRule({ ...newRule, agent_filter: e.target.value })}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
          />
          <div className="flex gap-3">
            <button
              onClick={handleCreate}
              disabled={!newRule.name.trim()}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
            >
              Create Rule
            </button>
            <button onClick={() => setShowCreate(false)} className="px-4 py-2 text-gray-500 text-sm">
              Cancel
            </button>
          </div>
        </div>
      )}

      {data?.alerts && data.alerts.length > 0 && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-5">
          <h2 className="text-sm font-semibold text-red-800 mb-3 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" />
            Active Alerts ({data.alerts.length})
          </h2>
          <div className="space-y-2">
            {data.alerts.map((alert: AlertEvent) => (
              <div key={alert.id} className="flex items-center justify-between bg-white rounded-lg p-3 border border-red-100">
                <div>
                  <span className="text-sm font-medium text-gray-900">{alert.agent_id}</span>
                  <p className="text-xs text-gray-500">
                    {alert.details ? JSON.stringify(alert.details) : ""}
                  </p>
                </div>
                <span className="text-xs text-gray-400">
                  {new Date(alert.triggered_at).toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h2 className="text-sm font-semibold text-gray-700 mb-4 flex items-center gap-2">
          <Bell className="w-4 h-4" />
          Alert Rules
        </h2>
        {data?.rules?.length === 0 ? (
          <p className="text-gray-400 text-sm text-center py-4">No rules configured</p>
        ) : (
          <div className="space-y-2">
            {data?.rules?.map((rule: AlertRule) => (
              <div key={rule.id} className="flex items-center justify-between py-2 border-b border-gray-100 last:border-0">
                <div>
                  <span className="text-sm font-medium text-gray-900">{rule.name}</span>
                  <p className="text-xs text-gray-500">
                    {CONDITION_LABELS[rule.condition_type]} · Threshold: {rule.threshold} · Channel: {rule.channel}
                    {rule.agent_filter && ` · Agent: ${rule.agent_filter}`}
                  </p>
                </div>
                <button
                  onClick={() => handleToggle(rule)}
                  className={`px-3 py-1 text-xs font-medium rounded-full ${
                    rule.active ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"
                  }`}
                >
                  {rule.active ? "Active" : "Disabled"}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
