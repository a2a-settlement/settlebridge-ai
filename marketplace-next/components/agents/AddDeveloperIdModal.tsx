"use client";

import { useState } from "react";
import { AlertCircle, Eye, EyeOff, Key, X } from "lucide-react";
import api from "@/services/api";
import {
  extractApiError,
  type AttachDeveloperIdResponse,
} from "@/lib/managedAgents";

interface Props {
  onClose: () => void;
  onAttached: (developerId: string) => void;
}

export default function AddDeveloperIdModal({ onClose, onAttached }: Props) {
  const [agentApiKey, setAgentApiKey] = useState("");
  const [keyVisible, setKeyVisible] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const handleClose = () => {
    setAgentApiKey("");
    setKeyVisible(false);
    onClose();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const keyToVerify = agentApiKey.trim();
    // Clear key from React state immediately upon submit; never log it.
    setAgentApiKey("");
    setKeyVisible(false);

    if (!keyToVerify) {
      setError("Enter an existing agent API key to prove control.");
      return;
    }

    setSubmitting(true);
    setError("");
    try {
      const { data } = await api.post<AttachDeveloperIdResponse>(
        "/bots/developer-ids/attach",
        { agent_api_key: keyToVerify }
      );
      onAttached(data.developer_id);
    } catch (err: unknown) {
      setError(extractApiError(err, "Failed to attach Developer ID"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40">
      <div
        className="bg-white border border-gray-200 rounded-xl max-w-md w-full p-6 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="attach-dev-id-title"
      >
        <div className="flex items-center justify-between gap-3 mb-3">
          <div className="flex items-center gap-2 text-navy-900 font-semibold">
            <Key className="w-4 h-4 text-money-dark" />
            <h3 id="attach-dev-id-title">Add Developer ID</h3>
          </div>
          <button
            type="button"
            onClick={handleClose}
            disabled={submitting}
            aria-label="Close modal"
            className="p-1 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-navy-500"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <p className="text-xs text-gray-600 leading-relaxed mb-4">
          Paste the API key (<code className="bg-gray-100 px-1 rounded">ate_…</code>) of an agent you control that is already registered under the Developer ID you want to attach. If no operator has claimed that Developer ID yet, use the key of the oldest agent registered under it. The key proves control of that agent, is checked once, and is never stored. If another operator already holds the Developer ID, attach is rejected. A key does not let you register a new agent into someone else&apos;s Developer ID.
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
              htmlFor="attach-agent-api-key"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              Existing agent API key
            </label>
            <div className="relative">
              <input
                id="attach-agent-api-key"
                type={keyVisible ? "text" : "password"}
                required
                autoComplete="off"
                spellCheck={false}
                value={agentApiKey}
                onChange={(e) => setAgentApiKey(e.target.value)}
                placeholder="ate_…"
                className="w-full pr-10 pl-3 py-2.5 rounded-lg border border-gray-300 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-navy-500"
              />
              <button
                type="button"
                onClick={() => setKeyVisible((v) => !v)}
                aria-label={keyVisible ? "Hide API key" : "Show API key"}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 p-1 rounded focus:outline-none focus:ring-2 focus:ring-navy-500"
              >
                {keyVisible ? (
                  <EyeOff className="w-4 h-4" />
                ) : (
                  <Eye className="w-4 h-4" />
                )}
              </button>
            </div>
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={handleClose}
              disabled={submitting}
              className="px-4 py-2 text-sm font-medium text-gray-600 hover:text-gray-900 rounded-lg focus:outline-none focus:ring-2 focus:ring-navy-500"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-4 py-2 bg-navy-900 text-white rounded-lg text-sm font-semibold hover:bg-navy-800 transition disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-navy-500"
            >
              {submitting ? "Verifying…" : "Attach Developer ID"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
