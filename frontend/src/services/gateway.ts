import api from "./api";
import type {
  AgentDetail,
  AgentHealth,
  AlertListResponse,
  AlertRule,
  AuditListResponse,
  ExchangeAgentResult,
  GatewayAgentClaim,
  GatewayHealth,
  GatewayMetrics,
  PolicyValidationResult,
  SettlementOverview,
  TrustPolicy,
} from "../types/gateway";

// --- Health ---

export async function fetchGatewayHealth(): Promise<GatewayHealth> {
  const { data } = await api.get<GatewayHealth>("/gateway/health");
  return data;
}

// --- Agents ---

export async function fetchAgents(): Promise<AgentHealth[]> {
  const { data } = await api.get<AgentHealth[]>("/gateway/agents");
  return data;
}

export async function fetchAgentDetail(agentId: string): Promise<AgentDetail> {
  const { data } = await api.get<AgentDetail>(`/gateway/agents/${agentId}/history`);
  return data;
}

// --- Transactions ---

export async function fetchTransactions(params?: {
  source?: string;
  target?: string;
  decision?: string;
  page?: number;
  page_size?: number;
}): Promise<AuditListResponse> {
  const { data } = await api.get<AuditListResponse>("/gateway/transactions", { params });
  return data;
}

// --- Audit ---

export async function fetchAuditLog(params?: {
  source?: string;
  target?: string;
  decision?: string;
  page?: number;
  page_size?: number;
}): Promise<AuditListResponse> {
  const { data } = await api.get<AuditListResponse>("/gateway/audit", { params });
  return data;
}

export async function exportAudit(format: "json" | "csv" = "json"): Promise<string> {
  const { data } = await api.get<string>("/gateway/audit/export", {
    params: { format },
    responseType: format === "csv" ? "text" : "json",
  });
  return typeof data === "string" ? data : JSON.stringify(data, null, 2);
}

// --- Policies ---

export async function fetchPolicies(): Promise<TrustPolicy[]> {
  const { data } = await api.get<TrustPolicy[]>("/gateway/policies");
  return data;
}

export async function createPolicy(name: string, yaml_content: string): Promise<TrustPolicy> {
  const { data } = await api.post<TrustPolicy>("/gateway/policies", { name, yaml_content });
  return data;
}

export async function deletePolicy(policyId: string): Promise<void> {
  await api.delete(`/gateway/policies/${policyId}`);
}

export async function validatePolicy(
  name: string,
  yaml_content: string
): Promise<PolicyValidationResult> {
  const { data } = await api.post<PolicyValidationResult>("/gateway/policies/validate", {
    name,
    yaml_content,
  });
  return data;
}

// --- Settlement ---

export async function fetchSettlementOverview(): Promise<SettlementOverview> {
  const { data } = await api.get<SettlementOverview>("/gateway/settlement/overview");
  return data;
}

// --- Alerts ---

export async function fetchAlerts(): Promise<AlertListResponse> {
  const { data } = await api.get<AlertListResponse>("/gateway/alerts");
  return data;
}

export async function createAlertRule(rule: Omit<AlertRule, "id" | "active" | "created_at">): Promise<AlertRule> {
  const { data } = await api.post<AlertRule>("/gateway/alerts/rules", rule);
  return data;
}

export async function updateAlertRule(
  ruleId: string,
  updates: Partial<AlertRule>
): Promise<AlertRule> {
  const { data } = await api.put<AlertRule>(`/gateway/alerts/rules/${ruleId}`, updates);
  return data;
}

// --- Agent Claims ---

export async function fetchClaimedAgents(): Promise<GatewayAgentClaim[]> {
  const { data } = await api.get<GatewayAgentClaim[]>("/gateway/agents/claimed");
  return data;
}

export async function claimAgent(
  exchange_account_id: string,
  agent_api_key?: string
): Promise<GatewayAgentClaim> {
  const { data } = await api.post<GatewayAgentClaim>("/gateway/agents/claim", {
    exchange_account_id,
    agent_api_key: agent_api_key || undefined,
  });
  return data;
}

export async function unclaimAgent(exchange_account_id: string): Promise<void> {
  await api.delete(`/gateway/agents/${exchange_account_id}/unclaim`);
}

export async function searchExchangeDirectory(
  q?: string
): Promise<ExchangeAgentResult[]> {
  const { data } = await api.get<ExchangeAgentResult[]>(
    "/gateway/agents/exchange-directory",
    { params: q ? { q } : {} }
  );
  return data;
}

// --- Metrics ---

export async function fetchMetrics(): Promise<GatewayMetrics> {
  const { data } = await api.get<GatewayMetrics>("/gateway/metrics");
  return data;
}

// --- Polling hook ---

import { useEffect, useState, useCallback, useRef } from "react";

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number = 5000
): { data: T | null; loading: boolean; error: Error | null; refresh: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const mountedRef = useRef(true);

  const load = useCallback(async () => {
    try {
      const result = await fetcher();
      if (mountedRef.current) {
        setData(result);
        setError(null);
      }
    } catch (e) {
      if (mountedRef.current) {
        setError(e instanceof Error ? e : new Error(String(e)));
      }
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [fetcher]);

  useEffect(() => {
    mountedRef.current = true;
    load();
    const timer = setInterval(load, intervalMs);
    return () => {
      mountedRef.current = false;
      clearInterval(timer);
    };
  }, [load, intervalMs]);

  return { data, loading, error, refresh: load };
}
