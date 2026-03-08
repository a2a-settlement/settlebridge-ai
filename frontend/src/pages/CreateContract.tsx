import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, CalendarClock } from "lucide-react";
import api from "../services/api";

const SCHEDULE_PRESETS = [
  { label: "Every hour", cron: "0 * * * *", desc: "Every hour at :00" },
  { label: "Every 6 hours", cron: "0 */6 * * *", desc: "Every 6 hours" },
  { label: "Daily (9 AM UTC)", cron: "0 9 * * *", desc: "Every day at 9:00 AM UTC" },
  { label: "Weekly (Mon 9 AM)", cron: "0 9 * * 1", desc: "Every Monday at 9:00 AM UTC" },
  { label: "Bi-weekly (Mon 9 AM)", cron: "0 9 * * 1", desc: "Every other Monday at 9:00 AM UTC" },
  { label: "Monthly (1st, 9 AM)", cron: "0 9 1 * *", desc: "1st of every month at 9:00 AM UTC" },
  { label: "Custom", cron: "", desc: "" },
];

const PROVENANCE_TIERS = [
  { value: "tier1_self_declared", label: "Tier 1 — Self-Declared" },
  { value: "tier2_signed", label: "Tier 2 — Signed" },
  { value: "tier3_verifiable", label: "Tier 3 — Verifiable" },
];

export default function CreateContract() {
  const navigate = useNavigate();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedPreset, setSelectedPreset] = useState(3);

  const [form, setForm] = useState({
    title: "",
    description: "",
    agent_user_id: "",
    agent_exchange_bot_id: "",
    reward_per_snapshot: 50,
    schedule: SCHEDULE_PRESETS[3].cron,
    schedule_description: SCHEDULE_PRESETS[3].desc,
    max_snapshots: "",
    grace_period_hours: 24,
    auto_approve: true,
    provenance_tier: "tier1_self_declared",
    acceptance_description: "",
    acceptance_output_format: "",
  });

  function updateField(field: string, value: unknown) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  function selectPreset(idx: number) {
    setSelectedPreset(idx);
    const preset = SCHEDULE_PRESETS[idx];
    if (preset.cron) {
      updateField("schedule", preset.cron);
      updateField("schedule_description", preset.desc);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);

    try {
      const payload: Record<string, unknown> = {
        title: form.title,
        description: form.description,
        agent_user_id: form.agent_user_id,
        agent_exchange_bot_id: form.agent_exchange_bot_id,
        reward_per_snapshot: form.reward_per_snapshot,
        schedule: form.schedule,
        schedule_description: form.schedule_description,
        grace_period_hours: form.grace_period_hours,
        auto_approve: form.auto_approve,
        provenance_tier: form.provenance_tier,
      };

      if (form.max_snapshots) {
        payload.max_snapshots = parseInt(form.max_snapshots, 10);
      }

      if (form.acceptance_description) {
        payload.acceptance_criteria = {
          description: form.acceptance_description,
          output_format: form.acceptance_output_format,
        };
      }

      const { data } = await api.post("/contracts", payload);
      navigate(`/contracts/${data.id}`);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Failed to create contract");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <button
        onClick={() => navigate("/contracts")}
        className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-navy-900 mb-6 transition"
      >
        <ArrowLeft className="w-4 h-4" /> Back to Contracts
      </button>

      <h1 className="text-2xl font-bold text-navy-900 mb-2">Create Service Contract</h1>
      <p className="text-gray-500 text-sm mb-8">
        Set up a recurring service with an agent. Define the schedule, deliverables, and payment per snapshot.
      </p>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 mb-6 text-sm">
          {typeof error === "string" ? error : JSON.stringify(error)}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-6">
        <div className="bg-white border border-gray-200 rounded-xl p-6 space-y-5">
          <h2 className="font-semibold text-navy-900">Basic Details</h2>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Title</label>
            <input
              required
              value={form.title}
              onChange={(e) => updateField("title", e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-navy-500"
              placeholder="e.g. Weekly GovCon Sentiment Report"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
            <textarea
              required
              rows={3}
              value={form.description}
              onChange={(e) => updateField("description", e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-navy-500"
              placeholder="Describe what the agent should deliver each cycle..."
            />
          </div>

          <div className="grid sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Agent User ID</label>
              <input
                required
                value={form.agent_user_id}
                onChange={(e) => updateField("agent_user_id", e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-navy-500"
                placeholder="UUID"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Agent Exchange Bot ID</label>
              <input
                required
                value={form.agent_exchange_bot_id}
                onChange={(e) => updateField("agent_exchange_bot_id", e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-navy-500"
                placeholder="Exchange bot ID"
              />
            </div>
          </div>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 space-y-5">
          <h2 className="font-semibold text-navy-900 flex items-center gap-2">
            <CalendarClock className="w-5 h-5" /> Schedule & Payment
          </h2>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Schedule</label>
            <div className="flex flex-wrap gap-2 mb-3">
              {SCHEDULE_PRESETS.map((preset, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => selectPreset(idx)}
                  className={`px-3 py-1.5 rounded-full text-xs font-medium transition ${
                    selectedPreset === idx
                      ? "bg-navy-900 text-white"
                      : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                  }`}
                >
                  {preset.label}
                </button>
              ))}
            </div>
            {selectedPreset === SCHEDULE_PRESETS.length - 1 && (
              <div className="grid sm:grid-cols-2 gap-3">
                <input
                  required
                  value={form.schedule}
                  onChange={(e) => updateField("schedule", e.target.value)}
                  className="border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-navy-500"
                  placeholder="0 9 * * 1"
                />
                <input
                  required
                  value={form.schedule_description}
                  onChange={(e) => updateField("schedule_description", e.target.value)}
                  className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-navy-500"
                  placeholder="Every Monday at 9 AM UTC"
                />
              </div>
            )}
          </div>

          <div className="grid sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Reward per Snapshot (ATE)</label>
              <input
                required
                type="number"
                min={1}
                value={form.reward_per_snapshot}
                onChange={(e) => updateField("reward_per_snapshot", parseInt(e.target.value) || 0)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-navy-500"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Max Snapshots</label>
              <input
                type="number"
                min={1}
                value={form.max_snapshots}
                onChange={(e) => updateField("max_snapshots", e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-navy-500"
                placeholder="Unlimited"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Grace Period (hours)</label>
              <input
                required
                type="number"
                min={1}
                value={form.grace_period_hours}
                onChange={(e) => updateField("grace_period_hours", parseInt(e.target.value) || 24)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-navy-500"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Provenance Tier</label>
            <select
              value={form.provenance_tier}
              onChange={(e) => updateField("provenance_tier", e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-navy-500"
            >
              {PROVENANCE_TIERS.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.auto_approve}
              onChange={(e) => updateField("auto_approve", e.target.checked)}
              className="rounded"
            />
            <span className="text-gray-700">Auto-approve deliveries via mediator (when confidence &ge; 80%)</span>
          </label>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 space-y-5">
          <h2 className="font-semibold text-navy-900">Acceptance Criteria (optional)</h2>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
            <textarea
              rows={2}
              value={form.acceptance_description}
              onChange={(e) => updateField("acceptance_description", e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-navy-500"
              placeholder="What constitutes an acceptable snapshot delivery?"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Output Format</label>
            <input
              value={form.acceptance_output_format}
              onChange={(e) => updateField("acceptance_output_format", e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-navy-500"
              placeholder="e.g. json, csv, markdown"
            />
          </div>
        </div>

        <div className="flex justify-end gap-3">
          <button
            type="button"
            onClick={() => navigate("/contracts")}
            className="px-5 py-2.5 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="px-5 py-2.5 bg-navy-900 text-white rounded-lg text-sm font-semibold hover:bg-navy-800 transition disabled:opacity-50"
          >
            {submitting ? "Creating..." : "Create Contract"}
          </button>
        </div>
      </form>
    </div>
  );
}
