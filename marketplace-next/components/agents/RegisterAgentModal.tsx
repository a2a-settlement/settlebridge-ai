"use client";

import { useState } from "react";
import { AlertCircle, Plus, X } from "lucide-react";
import api from "@/services/api";
import {
  extractApiError,
  type RegisterAgentPayload,
  type RegisterAgentResponse,
  type RotatedKeyResult,
} from "@/lib/managedAgents";

interface Props {
  developerIds: string[];
  initialDeveloperId?: string | null;
  onClose: () => void;
  onRegistered: (revealedKey: RotatedKeyResult) => void;
}

export default function RegisterAgentModal({
  developerIds,
  initialDeveloperId,
  onClose,
  onRegistered,
}: Props) {
  const defaultDevId =
    (initialDeveloperId && developerIds.includes(initialDeveloperId)
      ? initialDeveloperId
      : developerIds[0]) ??
    initialDeveloperId ??
    "";

  const [developerId, setDeveloperId] = useState(defaultDevId);
  const [agentName, setAgentName] = useState("");
  const [description, setDescription] = useState("");
  const [skillsInput, setSkillsInput] = useState("");
  const [isTest, setIsTest] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedName = agentName.trim();
    const trimmedDevId = developerId.trim();
    if (!trimmedName) {
      setError("Agent name is required.");
      return;
    }
    if (!trimmedDevId) {
      setError("Developer ID is required.");
      return;
    }

    const skills = skillsInput
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);

    const payload: RegisterAgentPayload = {
      bot_name: trimmedName,
      developer_id: trimmedDevId,
      is_test: isTest,
    };
    if (description.trim()) {
      payload.description = description.trim();
    }
    if (skills.length > 0) {
      payload.skills = skills;
    }

    setSubmitting(true);
    setError("");
    try {
      const { data } = await api.post<RegisterAgentResponse>(
        "/bots/register",
        payload
      );
      onRegistered({
        bot_id: data.account.id,
        bot_name: data.account.bot_name,
        api_key: data.api_key,
        grace_period_minutes: 0,
        warning:
          "Store this API key immediately — it will not be shown again.",
      });
    } catch (err: unknown) {
      setError(extractApiError(err, "Failed to register agent"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40">
      <div
        className="bg-white border border-gray-200 rounded-xl max-w-lg w-full p-6 shadow-xl max-h-[90vh] overflow-y-auto"
        role="dialog"
        aria-modal="true"
        aria-labelledby="register-agent-title"
      >
        <div className="flex items-center justify-between gap-3 mb-4">
          <div className="flex items-center gap-2 text-navy-900 font-semibold">
            <Plus className="w-4 h-4 text-money-dark" />
            <h3 id="register-agent-title">Register agent</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            aria-label="Close modal"
            className="p-1 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-navy-500"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <p className="text-xs text-gray-500 mb-4">
          Creates a new exchange identity under a Developer ID you control. Its API key will be shown once right after registration.
        </p>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg p-3 mb-4 flex items-start gap-2 text-sm">
            <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label
              htmlFor="register-agent-dev-id"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              Developer ID
            </label>
            {developerIds.length > 0 ? (
              <select
                id="register-agent-dev-id"
                value={developerId}
                onChange={(e) => setDeveloperId(e.target.value)}
                className="w-full px-3 py-2.5 rounded-lg border border-gray-300 bg-white text-sm font-mono focus:outline-none focus:ring-2 focus:ring-navy-500"
              >
                {developerIds.map((id) => (
                  <option key={id} value={id}>
                    {id}
                  </option>
                ))}
              </select>
            ) : (
              <input
                id="register-agent-dev-id"
                type="text"
                required
                value={developerId}
                onChange={(e) => setDeveloperId(e.target.value)}
                className="w-full px-3 py-2.5 rounded-lg border border-gray-300 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-navy-500"
                placeholder="your-developer-id"
              />
            )}
          </div>

          <div>
            <label
              htmlFor="register-agent-name"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              Agent name
            </label>
            <input
              id="register-agent-name"
              type="text"
              required
              value={agentName}
              onChange={(e) => setAgentName(e.target.value)}
              placeholder="e.g. Security Triage Agent"
              className="w-full px-3 py-2.5 rounded-lg border border-gray-300 text-sm focus:outline-none focus:ring-2 focus:ring-navy-500"
            />
          </div>

          <div>
            <label
              htmlFor="register-agent-description"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              Description <span className="text-gray-400 font-normal">(optional)</span>
            </label>
            <textarea
              id="register-agent-description"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Briefly describe what this agent handles…"
              className="w-full px-3 py-2 rounded-lg border border-gray-300 text-sm focus:outline-none focus:ring-2 focus:ring-navy-500"
            />
          </div>

          <div>
            <label
              htmlFor="register-agent-skills"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              Skills <span className="text-gray-400 font-normal">(optional, comma-separated)</span>
            </label>
            <input
              id="register-agent-skills"
              type="text"
              value={skillsInput}
              onChange={(e) => setSkillsInput(e.target.value)}
              placeholder="code-review, python, vulnerability-fix"
              className="w-full px-3 py-2.5 rounded-lg border border-gray-300 text-sm focus:outline-none focus:ring-2 focus:ring-navy-500"
            />
          </div>

          <label
            htmlFor="register-agent-is-test"
            className="flex items-start gap-2.5 text-sm text-gray-700 cursor-pointer pt-1"
          >
            <input
              id="register-agent-is-test"
              type="checkbox"
              checked={isTest}
              onChange={(e) => setIsTest(e.target.checked)}
              className="mt-0.5 rounded focus:ring-2 focus:ring-navy-500"
            />
            <span>
              Mark as test agent{" "}
              <span className="block text-xs text-gray-500">
                Hidden from default directory and agent list views unless test filters are enabled.
              </span>
            </span>
          </label>

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="px-4 py-2 text-sm font-medium text-gray-600 hover:text-gray-900 rounded-lg focus:outline-none focus:ring-2 focus:ring-navy-500"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-4 py-2 bg-money text-navy-900 rounded-lg text-sm font-semibold hover:bg-money-dark transition disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-navy-500"
            >
              {submitting ? "Registering…" : "Register agent"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
