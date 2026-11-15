export interface GatewayClaimInfo {
  gateway_id: string;
  gateway_name: string;
  verified: boolean;
  claimed_at: string;
}

export interface ManagedAgent {
  id: string;
  bot_name: string;
  status: string;
  developer_id: string;
  reputation?: number;
  created_at?: string;
  last_activity_at?: string;
  is_test?: boolean;
  own?: boolean;
  has_agent_card?: boolean;
  description?: string;
  skills?: string[];
  agent_card?: Record<string, unknown> | null;
  gateway_claims?: GatewayClaimInfo[] | null;
}

export interface ManagedAgentsResponse {
  developer_id: string | null;
  developer_ids?: string[];
  caller_account_type?: "agent" | "gateway";
  bots: ManagedAgent[];
  gateway_claimed_agents?: ManagedAgent[];
  count: number;
}

export interface RotatedKeyResult {
  bot_id: string;
  bot_name: string;
  api_key: string;
  grace_period_minutes: number;
  warning: string;
}

export interface RegisterAgentPayload {
  bot_name: string;
  developer_id: string;
  description?: string;
  skills?: string[];
  is_test?: boolean;
}

export interface RegisterAgentResponse {
  account: {
    id: string;
    bot_name: string;
    developer_id: string;
  };
  api_key: string;
  starter_tokens: number;
}

export interface AttachDeveloperIdResponse {
  developer_id: string;
  owner_account_id: string;
  status: string;
}

export interface DetachDeveloperIdResponse {
  developer_id: string;
  status: string;
}

export interface PublishAgentCardResponse {
  agent_id: string;
  kya_level_verified: number;
  card: Record<string, unknown>;
}

export interface AgentSettlement {
  id: string;
  tx_type: string;
  amount: number;
  direction: "in" | "out";
  escrow_id?: string | null;
  description?: string | null;
  created_at?: string | null;
}

export interface AgentSettlementsResponse {
  account_id: string;
  settlements: AgentSettlement[];
}

export function truncateAgentId(id: string, head = 8, tail = 6): string {
  if (id.length <= head + tail + 3) return id;
  return `${id.slice(0, head)}…${id.slice(-tail)}`;
}

export function agentCounts(bots: ManagedAgent[]) {
  let active = 0;
  let suspended = 0;
  let missingCard = 0;
  for (const b of bots) {
    if (b.status === "active") active += 1;
    else if (b.status === "suspended") suspended += 1;
    if (b.has_agent_card === false) missingCard += 1;
  }
  return { active, suspended, missingCard };
}

/** Same exclusion as the My agents table: drop the operator's linked account. */
export function listedManagedAgents(
  bots: ManagedAgent[],
  ownAccountId: string | null | undefined
): ManagedAgent[] {
  return bots.filter((b) => !b.own && b.id !== ownAccountId);
}

export function hasGatewayTabAccess(params: {
  signedIn: boolean;
  bots: ManagedAgent[];
  callerAccountType?: "agent" | "gateway";
  gatewayClaimedAgents?: ManagedAgent[];
}): boolean {
  if (!params.signedIn) return false;
  if (params.callerAccountType === "gateway") return true;
  if ((params.gatewayClaimedAgents?.length ?? 0) > 0) return true;
  return params.bots.some((b) => (b.gateway_claims?.length ?? 0) > 0);
}

export function formatActivityDate(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function extractApiError(err: unknown, fallback: string): string {
  const detail = (
    err as {
      response?: {
        data?: {
          detail?:
            | string
            | Array<{ loc?: Array<string | number>; msg?: string }>;
        };
      };
    }
  )?.response?.data?.detail;

  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const formatted = detail
      .map((item) => {
        const path = Array.isArray(item?.loc)
          ? item.loc.filter((p) => p !== "body").join(".")
          : "";
        const msg = item?.msg || "Invalid value";
        return path ? `${path}: ${msg}` : msg;
      })
      .join("; ");
    if (formatted) return formatted;
  }
  return fallback;
}
