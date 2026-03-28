export type PolicyDecision = "approve" | "block" | "flag";
export type AgentStatus = "active" | "degraded" | "offline";
export type AlertConditionType =
  | "reputation_below"
  | "spending_approaching"
  | "error_rate_above"
  | "anomalous_volume"
  | "policy_violation_spike";
export type AlertChannel = "dashboard" | "webhook" | "email";

// --- Trust Policy ---

export interface TrustPolicy {
  id: string;
  name: string;
  yaml_content: string;
  version: number;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface PolicyValidationResult {
  valid: boolean;
  errors: string[];
  matched_transactions: number;
  would_block: number;
  would_flag: number;
}

// --- Audit ---

export interface AuditEntry {
  id: string;
  timestamp: string;
  request_hash: string;
  source_agent: string;
  target_agent: string;
  policy_decision: PolicyDecision;
  escrow_id: string | null;
  latency_ms: number | null;
  response_status: number | null;
  merkle_root: string | null;
  details: Record<string, unknown> | null;
}

export interface AuditListResponse {
  entries: AuditEntry[];
  total: number;
  page: number;
  page_size: number;
}

// --- Reputation ---

export interface ReputationSnapshot {
  id: string;
  agent_id: string;
  bot_id: string;
  reputation_score: number;
  snapshot_at: string;
}

// --- Agent Health ---

export interface AgentHealth {
  agent_id: string;
  bot_id: string;
  status: AgentStatus;
  reputation_score: number | null;
  avg_latency_ms: number | null;
  error_rate: number | null;
  request_count: number;
  last_seen: string | null;
}

export interface AgentDetail extends AgentHealth {
  reputation_history: ReputationSnapshot[];
  recent_transactions: AuditEntry[];
}

// --- Alerts ---

export interface AlertRule {
  id: string;
  name: string;
  condition_type: AlertConditionType;
  threshold: number;
  channel: AlertChannel;
  agent_filter: string | null;
  active: boolean;
  created_at: string;
}

export interface AlertEvent {
  id: string;
  rule_id: string;
  agent_id: string;
  triggered_at: string;
  resolved_at: string | null;
  details: Record<string, unknown> | null;
}

export interface AlertListResponse {
  alerts: AlertEvent[];
  rules: AlertRule[];
}

// --- Gateway Agent Claims ---

export interface GatewayAgentClaim {
  id: string;
  exchange_account_id: string;
  bot_name: string;
  description: string | null;
  skills: string[] | null;
  exchange_claim_id: string | null;
  verified: boolean;
  claimed_at: string;
  status: string;
}

export interface ExchangeAgentResult {
  id: string;
  bot_name: string;
  developer_name: string;
  description: string | null;
  skills: string[];
  reputation: number;
  already_claimed: boolean;
}

// --- Gateway Overview ---

export interface GatewayHealth {
  status: string;
  uptime_seconds: number;
  active_agents: number;
  total_transactions: number;
  policy_violations_24h: number;
  avg_latency_ms: number;
  exchange_connected: boolean;
}

export interface SettlementOverview {
  active_escrows: number;
  total_locked: number;
  total_released: number;
  total_disputed: number;
  treasury_fees: number;
  top_agents: Record<string, unknown>[];
}

export interface GatewayMetrics {
  transactions_per_hour: number;
  requests_by_decision: Record<string, number>;
  error_rate: number;
  avg_latency_ms: number;
  cache_hit_rate: number;
}
