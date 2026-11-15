"use client";

import { useMemo } from "react";
import Link from "next/link";
import {
  CheckCircle2,
  PauseCircle,
  Server,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import ReputationScore from "@/components/ReputationScore";
import {
  truncateAgentId,
  type GatewayClaimInfo,
  type ManagedAgent,
} from "@/lib/managedAgents";

interface GatewayRow {
  agent: ManagedAgent;
  claim: GatewayClaimInfo;
}

interface GatewayGroup {
  gatewayId: string;
  gatewayName: string;
  rows: GatewayRow[];
}

interface Props {
  bots: ManagedAgent[];
  gatewayClaimedAgents: ManagedAgent[];
  callerAccountType?: "agent" | "gateway";
  callerGatewayId?: string | null;
  callerDisplayName?: string;
  loading?: boolean;
}

function AgentStatusBadge({ status }: { status?: string }) {
  const active = status === "active";
  return (
    <span
      className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium capitalize ${
        active
          ? "bg-green-100 text-green-800"
          : "bg-gray-100 text-gray-700"
      }`}
    >
      {active ? (
        <CheckCircle2 className="w-3 h-3" aria-hidden="true" />
      ) : (
        <PauseCircle className="w-3 h-3" aria-hidden="true" />
      )}
      {status || "unknown"}
    </span>
  );
}

function VerificationBadge({ verified }: { verified: boolean }) {
  if (verified) {
    return (
      <span className="inline-flex items-center gap-1 text-xs px-2.5 py-0.5 rounded-full font-medium bg-green-50 text-green-800 border border-green-200">
        <ShieldCheck className="w-3.5 h-3.5" aria-hidden="true" />
        Key-verified
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs px-2.5 py-0.5 rounded-full font-medium bg-amber-50 text-amber-800 border border-amber-200">
      <ShieldAlert className="w-3.5 h-3.5" aria-hidden="true" />
      Unverified claim
    </span>
  );
}

export default function GatewayAgentsTab({
  bots,
  gatewayClaimedAgents,
  callerAccountType,
  callerGatewayId,
  callerDisplayName,
  loading = false,
}: Props) {
  const groups = useMemo<GatewayGroup[]>(() => {
    const map = new Map<string, GatewayGroup>();
    const seenPairs = new Set<string>();

    const callerOwnAgent = bots.find(
      (b) => b.own || (callerGatewayId && b.id === callerGatewayId)
    );
    const defaultCallerGwName =
      callerOwnAgent?.bot_name || callerDisplayName || "Your gateway";

    const addRow = (agent: ManagedAgent, claim: GatewayClaimInfo) => {
      const pairKey = `${claim.gateway_id}::${agent.id}`;
      if (seenPairs.has(pairKey)) return;
      seenPairs.add(pairKey);

      const existing = map.get(claim.gateway_id);
      if (existing) {
        existing.rows.push({ agent, claim });
      } else {
        map.set(claim.gateway_id, {
          gatewayId: claim.gateway_id,
          gatewayName: claim.gateway_name || "Gateway",
          rows: [{ agent, claim }],
        });
      }
    };

    for (const agent of bots) {
      for (const claim of agent.gateway_claims ?? []) {
        addRow(agent, claim);
      }
    }

    for (const agent of gatewayClaimedAgents) {
      if (agent.gateway_claims && agent.gateway_claims.length > 0) {
        const matching = callerGatewayId
          ? agent.gateway_claims.filter((c) => c.gateway_id === callerGatewayId)
          : agent.gateway_claims;
        const claimsToUse =
          matching.length > 0 ? matching : agent.gateway_claims;
        for (const claim of claimsToUse) {
          addRow(agent, claim);
        }
      } else if (callerGatewayId) {
        addRow(agent, {
          gateway_id: callerGatewayId,
          gateway_name: defaultCallerGwName,
          verified: false,
          claimed_at: agent.created_at || "",
        });
      }
    }

    return [...map.values()];
  }, [
    bots,
    gatewayClaimedAgents,
    callerGatewayId,
    callerDisplayName,
  ]);

  if (loading && groups.length === 0) {
    return (
      <div className="space-y-3">
        {[1, 2].map((i) => (
          <div key={i} className="h-24 bg-gray-100 rounded-xl animate-pulse" />
        ))}
      </div>
    );
  }

  if (groups.length === 0) {
    return (
      <div className="text-center py-14 px-4 border border-dashed border-gray-300 rounded-xl">
        <Server className="w-10 h-10 mx-auto mb-3 text-gray-300" />
        <p className="text-sm font-medium text-navy-900 mb-1">
          No gateway claims yet
        </p>
        <p className="text-xs text-gray-500 max-w-md mx-auto">
          {callerAccountType === "gateway"
            ? "Your account is registered as a gateway, but no agents currently hold an active gateway claim."
            : "None of your agents currently report running behind a gateway."}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-lg font-bold text-navy-900">Gateway routing</h2>
        <p className="text-sm text-gray-500">
          Agents routed behind settlement or trust gateways and their claim verification status
        </p>
      </div>

      {groups.map((group) => (
        <section key={group.gatewayId} className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-navy-900">
                <Server className="w-4 h-4 text-navy-700" aria-hidden="true" />
                {group.gatewayName}
              </span>
              <span
                className="font-mono text-xs text-gray-500 bg-gray-100 px-2 py-0.5 rounded"
                title={group.gatewayId}
              >
                {truncateAgentId(group.gatewayId)}
              </span>
            </div>
            <span className="text-xs text-gray-500">
              {group.rows.length} {group.rows.length === 1 ? "agent" : "agents"}
            </span>
          </div>

          {/* Desktop table (>= md / 768px, fits cleanly at 800px) */}
          <div className="hidden md:block overflow-x-auto border border-gray-200 rounded-xl">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-gray-500">
                <tr>
                  <th className="p-3 font-medium">Agent</th>
                  <th className="p-3 font-medium">Verification</th>
                  <th className="p-3 font-medium">Status</th>
                  <th className="p-3 font-medium">Reputation</th>
                  <th className="p-3 font-medium text-right">Profile</th>
                </tr>
              </thead>
              <tbody>
                {group.rows.map(({ agent, claim }) => (
                  <tr
                    key={`${group.gatewayId}-${agent.id}`}
                    className="border-t border-gray-100"
                  >
                    <td className="p-3">
                      <div className="font-semibold text-navy-900">
                        <Link
                          href={`/agents/${agent.id}`}
                          className="hover:underline focus:outline-none focus:ring-2 focus:ring-navy-500 rounded"
                        >
                          {agent.bot_name}
                        </Link>
                      </div>
                      <div
                        className="font-mono text-xs text-gray-500 mt-0.5"
                        title={agent.id}
                      >
                        {truncateAgentId(agent.id)}
                      </div>
                    </td>
                    <td className="p-3">
                      <VerificationBadge verified={claim.verified} />
                    </td>
                    <td className="p-3">
                      <AgentStatusBadge status={agent.status} />
                    </td>
                    <td className="p-3">
                      <ReputationScore
                        score={agent.reputation ?? null}
                        size="sm"
                      />
                    </td>
                    <td className="p-3 text-right">
                      <Link
                        href={`/agents/${agent.id}`}
                        className="text-xs font-medium text-navy-700 hover:text-navy-900 hover:underline focus:outline-none focus:ring-2 focus:ring-navy-500 rounded px-1"
                      >
                        View →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile cards (< md) */}
          <div className="md:hidden space-y-3">
            {group.rows.map(({ agent, claim }) => (
              <div
                key={`${group.gatewayId}-${agent.id}-mobile`}
                className="border border-gray-200 rounded-xl p-4 space-y-2.5 bg-white"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <Link
                      href={`/agents/${agent.id}`}
                      className="font-semibold text-navy-900 hover:underline text-sm block truncate"
                    >
                      {agent.bot_name}
                    </Link>
                    <span
                      className="font-mono text-xs text-gray-500"
                      title={agent.id}
                    >
                      {truncateAgentId(agent.id)}
                    </span>
                  </div>
                  <ReputationScore score={agent.reputation ?? null} size="sm" />
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <VerificationBadge verified={claim.verified} />
                  <AgentStatusBadge status={agent.status} />
                </div>
                <div>
                  <Link
                    href={`/agents/${agent.id}`}
                    className="text-xs font-medium text-navy-700 hover:underline inline-flex items-center gap-1"
                  >
                    Open agent profile →
                  </Link>
                </div>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
