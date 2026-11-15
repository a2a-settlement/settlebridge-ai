"use client";

import { useState } from "react";
import Link from "next/link";
import { Key, ShieldCheck } from "lucide-react";
import api from "@/services/api";
import type { RotatedKeyResult } from "@/lib/managedAgents";
import RotatedKeyReveal from "@/components/agents/RotatedKeyReveal";

interface GatewayClaimInfo {
  gateway_id: string;
  gateway_name: string;
  verified: boolean;
  claimed_at: string;
}

interface Props {
  agentId: string;
  agentName: string;
  status?: string;
  developerId?: string;
  gatewayClaims?: GatewayClaimInfo[] | null;
  hasAgentCard?: boolean;
  isOwnLinked: boolean;
  onUpdated: () => void;
}

export default function AgentManagePanel({
  agentId,
  agentName,
  status,
  developerId,
  gatewayClaims,
  hasAgentCard,
  isOwnLinked,
  onUpdated,
}: Props) {
  const [pending, setPending] = useState<"suspend" | "unsuspend" | "rotate" | null>(null);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState("");
  const [rotatedKey, setRotatedKey] = useState<RotatedKeyResult | null>(null);

  const runAction = async () => {
    if (!pending) return;
    setActing(true);
    setError("");
    try {
      if (pending === "rotate") {
        const res = await api.post<RotatedKeyResult>(`/bots/${agentId}/rotate-key`);
        setRotatedKey(res.data);
      } else if (pending === "suspend") {
        await api.post(`/bots/${agentId}/suspend`);
        onUpdated();
      } else if (pending === "unsuspend") {
        await api.post(`/bots/${agentId}/unsuspend`);
        onUpdated();
      }
      setPending(null);
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || "Action failed";
      setError(typeof detail === "string" ? detail : "Action failed");
    } finally {
      setActing(false);
    }
  };

  const confirmText =
    pending === "suspend"
      ? `Suspend "${agentName}"? New escrows stop until you reactivate this agent.`
      : pending === "unsuspend"
        ? `Reactivate "${agentName}"?`
        : pending === "rotate"
          ? `Rotate the API key for "${agentName}"? You will see the new key once.`
          : "";

  return (
  <>
    <div className="mt-8 border border-navy-200 rounded-xl bg-navy-50/50 p-5">
      <h3 className="font-semibold text-navy-900 text-sm mb-3">Manage</h3>
      {developerId && (
        <p className="text-xs text-gray-600 mb-3">
          Developer ID:{" "}
          <span className="font-mono text-gray-800 break-all">{developerId}</span>
        </p>
      )}
      {gatewayClaims && gatewayClaims.length > 0 && (
        <div className="mb-4">
          <p className="text-xs text-gray-500 mb-2">Runs behind</p>
          <div className="flex flex-wrap gap-2">
            {gatewayClaims.map((claim) => (
              <span
                key={claim.gateway_id}
                className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full bg-white border border-gray-200 text-navy-800"
              >
                <ShieldCheck className="w-3 h-3" />
                {claim.gateway_name || "Gateway"}
              </span>
            ))}
          </div>
        </div>
      )}
      {!hasAgentCard && (
        <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4">
          No Agent Card published — endpoints stay undiscoverable.{" "}
          <Link href="/developers" className="font-medium text-navy-800 hover:underline">
            Publish from developer docs
          </Link>{" "}
          or{" "}
          <Link href="/dashboard/settings" className="font-medium text-navy-800 hover:underline">
            Settings
          </Link>
          .
        </p>
      )}
      {error && <p className="text-sm text-red-600 mb-3">{error}</p>}
      <div className="flex flex-wrap gap-2">
        {status === "active" && !isOwnLinked && (
          <button
            type="button"
            className="text-sm px-3 py-2 rounded-lg bg-red-50 text-red-700 border border-red-200"
            onClick={() => setPending("suspend")}
          >
            Suspend
          </button>
        )}
        {status === "suspended" && (
          <button
            type="button"
            className="text-sm px-3 py-2 rounded-lg bg-green-50 text-green-700 border border-green-200"
            onClick={() => setPending("unsuspend")}
          >
            Reactivate
          </button>
        )}
        <button
          type="button"
          className="text-sm px-3 py-2 rounded-lg bg-white border border-gray-300 text-navy-900 inline-flex items-center gap-1.5"
          onClick={() => setPending("rotate")}
        >
          <Key className="w-3.5 h-3.5" />
          Rotate key
        </button>
      </div>
    </div>

    {pending && (
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40">
        <div className="bg-white rounded-xl max-w-md w-full p-6 border border-gray-200 shadow-xl">
          <h3 className="font-semibold text-navy-900 mb-2">
            {pending === "rotate" ? "Rotate key" : "Confirm"}
          </h3>
          <p className="text-sm text-gray-600 mb-6">{confirmText}</p>
          <div className="flex gap-3 justify-end">
            <button
              type="button"
              className="px-4 py-2 text-sm text-gray-600"
              onClick={() => setPending(null)}
              disabled={acting}
            >
              Cancel
            </button>
            <button
              type="button"
              className="px-4 py-2 text-sm bg-navy-900 text-white rounded-lg font-semibold disabled:opacity-50"
              onClick={() => void runAction()}
              disabled={acting}
            >
              {acting ? "Working…" : "Confirm"}
            </button>
          </div>
        </div>
      </div>
    )}

    {rotatedKey && (
      <RotatedKeyReveal
        rotatedKey={rotatedKey}
        onClose={() => {
          setRotatedKey(null);
          onUpdated();
        }}
      />
    )}
  </>
  );
}