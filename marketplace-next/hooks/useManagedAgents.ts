"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import api from "@/services/api";
import type { ManagedAgent, ManagedAgentsResponse } from "@/lib/managedAgents";

export function useManagedAgents(enabled: boolean) {
  const [data, setData] = useState<ManagedAgentsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const { data: res } = await api.get<ManagedAgentsResponse>("/bots");
      setData(res);
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || "Failed to load your agents";
      setError(typeof detail === "string" ? detail : "Failed to load your agents");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (enabled) void refresh();
  }, [enabled, refresh]);

  const bots = data?.bots ?? [];
  const ownedIds = useMemo(
    () => new Set(bots.map((b) => b.id)),
    [bots]
  );

  return {
    data,
    bots,
    developerId: data?.developer_id ?? null,
    ownedIds,
    loading,
    error,
    refresh,
  };
}

export function groupAgentsByDeveloperId(agents: ManagedAgent[]): Map<string, ManagedAgent[]> {
  const groups = new Map<string, ManagedAgent[]>();
  for (const agent of agents) {
    const dev = agent.developer_id || "unknown";
    const list = groups.get(dev) ?? [];
    list.push(agent);
    groups.set(dev, list);
  }
  return groups;
}