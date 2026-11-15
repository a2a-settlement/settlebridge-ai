"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import api from "@/services/api";
import {
  extractApiError,
  hasGatewayTabAccess,
  type ManagedAgent,
  type ManagedAgentsResponse,
} from "@/lib/managedAgents";

export function useManagedAgents(enabled: boolean) {
  const [data, setData] = useState<ManagedAgentsResponse | null>(null);
  const [inflight, setInflight] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setInflight(true);
    setError("");
    try {
      const { data: res } = await api.get<ManagedAgentsResponse>("/bots");
      setData(res);
    } catch (err: unknown) {
      setError(extractApiError(err, "Failed to load your agents"));
      setData(null);
    } finally {
      setInflight(false);
    }
  }, []);

  // True from the first enabled render until the first response, so counts
  // never paint as 0 before data exists. A later refresh stays inflight while
  // the previous payload is still shown.
  const loading = inflight || (enabled && data === null && error === "");

  useEffect(() => {
    if (enabled) void refresh();
  }, [enabled, refresh]);

  const bots = useMemo(() => data?.bots ?? [], [data]);
  const developerId = data?.developer_id ?? null;

  const developerIds = useMemo(() => {
    const out: string[] = [];
    const seen = new Set<string>();
    const push = (id: string | null | undefined) => {
      if (!id || seen.has(id)) return;
      seen.add(id);
      out.push(id);
    };
    push(developerId);
    for (const id of data?.developer_ids ?? []) {
      push(id);
    }
    return out;
  }, [developerId, data?.developer_ids]);

  const callerAccountType = data?.caller_account_type;
  const gatewayClaimedAgents = useMemo(
    () => data?.gateway_claimed_agents ?? [],
    [data?.gateway_claimed_agents]
  );

  const showGatewayTab = useMemo(
    () =>
      hasGatewayTabAccess({
        signedIn: enabled,
        bots,
        callerAccountType,
        gatewayClaimedAgents,
      }),
    [enabled, bots, callerAccountType, gatewayClaimedAgents]
  );

  const ownedIds = useMemo(() => new Set(bots.map((b) => b.id)), [bots]);

  return {
    data,
    bots,
    developerId,
    developerIds,
    callerAccountType,
    gatewayClaimedAgents,
    showGatewayTab,
    ownedIds,
    loading,
    error,
    refresh,
  };
}

export type ManagedAgentsState = ReturnType<typeof useManagedAgents>;

export function groupAgentsByDeveloperId(
  agents: ManagedAgent[],
  seedDeveloperIds?: string[]
): Map<string, ManagedAgent[]> {
  const groups = new Map<string, ManagedAgent[]>();
  if (seedDeveloperIds) {
    for (const devId of seedDeveloperIds) {
      if (devId && !groups.has(devId)) {
        groups.set(devId, []);
      }
    }
  }
  for (const agent of agents) {
    const dev = agent.developer_id || "unknown";
    const list = groups.get(dev) ?? [];
    list.push(agent);
    groups.set(dev, list);
  }
  return groups;
}
