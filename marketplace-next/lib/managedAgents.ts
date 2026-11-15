export interface ManagedAgent {
  id: string;
  bot_name: string;
  status: string;
  developer_id: string;
  reputation?: number;
  created_at?: string;
  is_test?: boolean;
  own?: boolean;
  has_agent_card?: boolean;
}

export interface ManagedAgentsResponse {
  developer_id: string | null;
  bots: ManagedAgent[];
  count: number;
}

export interface RotatedKeyResult {
  bot_id: string;
  bot_name: string;
  api_key: string;
  grace_period_minutes: number;
  warning: string;
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