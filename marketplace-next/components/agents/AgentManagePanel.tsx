"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ArrowDownLeft,
  ArrowUpRight,
  FileText,
  Key,
  ShieldCheck,
} from "lucide-react";
import api from "@/services/api";
import {
  extractApiError,
  formatActivityDate,
  truncateAgentId,
  type AgentSettlement,
  type AgentSettlementsResponse,
  type GatewayClaimInfo,
  type RotatedKeyResult,
} from "@/lib/managedAgents";
import RotatedKeyReveal from "@/components/agents/RotatedKeyReveal";
import PublishAgentCardModal from "@/components/agents/PublishAgentCardModal";

interface Props {
  agentId: string;
  agentName: string;
  status?: string;
  developerId?: string;
  gatewayClaims?: GatewayClaimInfo[] | null;
  hasAgentCard?: boolean;
  agentCard?: Record<string, unknown> | null;
  agentDescription?: string;
  agentSkills?: string[];
  isOwnLinked: boolean;
  onUpdated: () => void;
}

function formatTxType(txType: string): string {
  if (!txType) return "Settlement";
  return txType
    .replace(/_/g, " ")
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

export default function AgentManagePanel({
  agentId,
  agentName,
  status,
  developerId,
  gatewayClaims,
  hasAgentCard,
  agentCard,
  agentDescription,
  agentSkills,
  isOwnLinked,
  onUpdated,
}: Props) {
  const [pending, setPending] = useState<
    "suspend" | "unsuspend" | "rotate" | null
  >(null);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState("");
  const [rotatedKey, setRotatedKey] = useState<RotatedKeyResult | null>(null);
  const [cardModalOpen, setCardModalOpen] = useState(false);

  const [settlements, setSettlements] = useState<AgentSettlement[]>([]);
  const [settlementsLoading, setSettlementsLoading] = useState(true);
  const [settlementsError, setSettlementsError] = useState("");

  const loadSettlements = useCallback(async () => {
    setSettlementsLoading(true);
    setSettlementsError("");
    try {
      const { data } = await api.get<AgentSettlementsResponse>(
        `/bots/${encodeURIComponent(agentId)}/settlements`
      );
      setSettlements(data.settlements ?? []);
    } catch (err: unknown) {
      setSettlementsError(
        extractApiError(err, "Failed to load recent settlements")
      );
      setSettlements([]);
    } finally {
      setSettlementsLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    void loadSettlements();
  }, [loadSettlements]);

  const runAction = async () => {
    if (!pending) return;
    setActing(true);
    setError("");
    try {
      const encoded = encodeURIComponent(agentId);
      if (pending === "rotate") {
        const res = await api.post<RotatedKeyResult>(
          `/bots/${encoded}/rotate-key`
        );
        setRotatedKey(res.data);
      } else if (pending === "suspend") {
        await api.post(`/bots/${encoded}/suspend`);
        onUpdated();
      } else if (pending === "unsuspend") {
        await api.post(`/bots/${encoded}/unsuspend`);
        onUpdated();
      }
      setPending(null);
    } catch (err: unknown) {
      setError(extractApiError(err, "Action failed"));
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
      <div className="mt-8 border border-navy-200 rounded-xl bg-navy-50/50 p-5 space-y-5">
        <div>
          <h3 className="font-semibold text-navy-900 text-sm mb-3">Manage</h3>
          {developerId && (
            <p className="text-xs text-gray-600 mb-3">
              Developer ID:{" "}
              <span className="font-mono text-gray-800 break-all">
                {developerId}
              </span>
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
                    <ShieldCheck className="w-3 h-3" aria-hidden="true" />
                    {claim.gateway_name || "Gateway"}
                  </span>
                ))}
              </div>
            </div>
          )}
          {!hasAgentCard && (
            <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4">
              No Agent Card published — endpoints stay undiscoverable until you publish a card.
            </p>
          )}
          {error && <p className="text-sm text-red-600 mb-3">{error}</p>}
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setCardModalOpen(true)}
              className={
                hasAgentCard
                  ? "text-sm px-3 py-2 rounded-lg bg-white border border-gray-300 text-navy-900 inline-flex items-center gap-1.5 hover:bg-gray-50 transition focus:outline-none focus:ring-2 focus:ring-navy-500"
                  : "text-sm px-3.5 py-2 rounded-lg bg-money text-navy-900 font-semibold inline-flex items-center gap-1.5 hover:bg-money-dark transition focus:outline-none focus:ring-2 focus:ring-navy-500"
              }
            >
              <FileText className="w-3.5 h-3.5" aria-hidden="true" />
              {hasAgentCard ? "Edit Agent Card" : "Publish Agent Card"}
            </button>
            {status === "active" && !isOwnLinked && (
              <button
                type="button"
                className="text-sm px-3 py-2 rounded-lg bg-red-50 text-red-700 border border-red-200 hover:bg-red-100 transition focus:outline-none focus:ring-2 focus:ring-navy-500"
                onClick={() => setPending("suspend")}
              >
                Suspend
              </button>
            )}
            {status === "suspended" && (
              <button
                type="button"
                className="text-sm px-3 py-2 rounded-lg bg-green-50 text-green-700 border border-green-200 hover:bg-green-100 transition focus:outline-none focus:ring-2 focus:ring-navy-500"
                onClick={() => setPending("unsuspend")}
              >
                Reactivate
              </button>
            )}
            <button
              type="button"
              className="text-sm px-3 py-2 rounded-lg bg-white border border-gray-300 text-navy-900 inline-flex items-center gap-1.5 hover:bg-gray-50 transition focus:outline-none focus:ring-2 focus:ring-navy-500"
              onClick={() => setPending("rotate")}
            >
              <Key className="w-3.5 h-3.5" aria-hidden="true" />
              Rotate key
            </button>
          </div>
        </div>

        {/* Owner-scoped recent settlements */}
        <div className="border-t border-navy-200/70 pt-4">
          <h4 className="font-semibold text-navy-900 text-xs uppercase tracking-wide mb-3">
            Recent settlements
          </h4>
          {settlementsLoading ? (
            <div className="space-y-2">
              {[1, 2].map((i) => (
                <div
                  key={i}
                  className="h-11 bg-white/70 rounded-lg animate-pulse"
                />
              ))}
            </div>
          ) : settlementsError ? (
            <p className="text-xs text-red-600">{settlementsError}</p>
          ) : settlements.length === 0 ? (
            <p className="text-xs text-gray-500">
              No settlements recorded for this agent yet.
            </p>
          ) : (
            <div className="bg-white border border-gray-200 rounded-xl divide-y divide-gray-100">
              {settlements.map((tx) => {
                const inbound = tx.direction === "in";
                return (
                  <div
                    key={tx.id}
                    className="px-3.5 py-2.5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1.5 text-sm"
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full ${
                            inbound
                              ? "bg-green-50 text-green-800 border border-green-200"
                              : "bg-gray-100 text-gray-700 border border-gray-200"
                          }`}
                        >
                          {inbound ? (
                            <ArrowDownLeft
                              className="w-3 h-3"
                              aria-hidden="true"
                            />
                          ) : (
                            <ArrowUpRight
                              className="w-3 h-3"
                              aria-hidden="true"
                            />
                          )}
                          {inbound ? "Received" : "Sent"}
                        </span>
                        <span className="font-medium text-navy-900 text-xs sm:text-sm">
                          {formatTxType(tx.tx_type)}
                        </span>
                      </div>
                      {(tx.description || tx.escrow_id) && (
                        <p className="text-xs text-gray-500 mt-0.5 truncate">
                          {tx.description ||
                            (tx.escrow_id
                              ? `Escrow ${truncateAgentId(tx.escrow_id)}`
                              : "")}
                        </p>
                      )}
                    </div>
                    <div className="flex items-center justify-between sm:justify-end gap-3 flex-shrink-0">
                      <span
                        className={`font-semibold text-xs sm:text-sm ${
                          inbound ? "text-green-800" : "text-navy-900"
                        }`}
                      >
                        {inbound ? "+" : "−"}
                        {tx.amount} ATE
                      </span>
                      <span className="text-xs text-gray-400 whitespace-nowrap">
                        {formatActivityDate(tx.created_at)}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {pending && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40">
          <div
            className="bg-white rounded-xl max-w-md w-full p-6 border border-gray-200 shadow-xl"
            role="dialog"
            aria-modal="true"
          >
            <h3 className="font-semibold text-navy-900 mb-2">
              {pending === "rotate" ? "Rotate key" : "Confirm"}
            </h3>
            <p className="text-sm text-gray-600 mb-6">{confirmText}</p>
            <div className="flex gap-3 justify-end">
              <button
                type="button"
                className="px-4 py-2 text-sm text-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-navy-500"
                onClick={() => setPending(null)}
                disabled={acting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="px-4 py-2 text-sm bg-navy-900 text-white rounded-lg font-semibold disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-navy-500"
                onClick={() => void runAction()}
                disabled={acting}
              >
                {acting ? "Working…" : "Confirm"}
              </button>
            </div>
          </div>
        </div>
      )}

      {cardModalOpen && (
        <PublishAgentCardModal
          agentId={agentId}
          agentName={agentName}
          hasAgentCard={hasAgentCard}
          initialCard={agentCard ?? null}
          initialDescription={agentDescription}
          initialSkills={agentSkills}
          onClose={() => setCardModalOpen(false)}
          onPublished={() => {
            setCardModalOpen(false);
            onUpdated();
          }}
        />
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
