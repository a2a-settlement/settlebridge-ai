export type UserType = "requester" | "agent_operator" | "both";

export interface User {
  id: string;
  email: string;
  display_name: string;
  user_type: UserType;
  exchange_bot_id: string | null;
  exchange_balance_cached: number | null;
  bio: string | null;
  created_at: string;
}

export type BountyStatus =
  | "draft"
  | "open"
  | "claimed"
  | "submitted"
  | "in_review"
  | "completed"
  | "disputed"
  | "expired"
  | "cancelled";

export type Difficulty = "trivial" | "easy" | "medium" | "hard" | "expert";

export type ProvenanceTier =
  | "tier1_self_declared"
  | "tier2_signed"
  | "tier3_verifiable";

export interface Category {
  id: string;
  name: string;
  slug: string;
  description: string;
  icon: string | null;
  sort_order: number;
}

export interface PerformanceTranche {
  percent: number;
  indicator: string;
  measurement: string;
  escrow_duration_days: number;
  partial_credit: boolean;
}

export interface ReputationStake {
  enabled: boolean;
  weight: number;
}

export interface SettlementStructure {
  immediate_payout_percent: number;
  performance_tranches: PerformanceTranche[] | null;
  reputation_stake: ReputationStake | null;
}

export interface Bounty {
  id: string;
  requester_id: string;
  title: string;
  description: string;
  category_id: string | null;
  category: Category | null;
  tags: string[] | null;
  acceptance_criteria: Record<string, unknown> | null;
  reward_amount: number;
  escrow_id: string | null;
  status: BountyStatus;
  deadline: string | null;
  max_claims: number;
  min_reputation: number | null;
  difficulty: Difficulty;
  auto_approve: boolean;
  provenance_tier: ProvenanceTier;
  settlement_structure: SettlementStructure | null;
  created_at: string;
  updated_at: string;
  funded_at: string | null;
  completed_at: string | null;
}

export interface BountyListResponse {
  bounties: Bounty[];
  total: number;
  page: number;
  page_size: number;
}

export type ClaimStatus =
  | "active"
  | "submitted"
  | "accepted"
  | "rejected"
  | "abandoned"
  | "expired";

export interface Claim {
  id: string;
  bounty_id: string;
  agent_user_id: string;
  agent_exchange_bot_id: string;
  status: ClaimStatus;
  claimed_at: string;
  submitted_at: string | null;
  resolved_at: string | null;
  abandon_reason: string | null;
}

export type SubmissionStatus =
  | "pending_review"
  | "approved"
  | "rejected"
  | "disputed"
  | "partially_approved";

export interface AiReview {
  score: number;
  recommendation: "approve" | "partial_approve" | "reject";
  holdback: boolean;
  holdback_percent?: number;
  efficacy_criteria?: string;
  issues?: string[];
  notes: string;
  model?: string;
}

export interface Submission {
  id: string;
  claim_id: string;
  bounty_id: string;
  agent_user_id: string;
  deliverable: Record<string, unknown>;
  provenance: Record<string, unknown> | null;
  status: SubmissionStatus;
  reviewer_notes: string | null;
  submitted_at: string;
  reviewed_at: string | null;
  score: number | null;
  release_percent: number | null;
  efficacy_check_at: string | null;
  efficacy_criteria: string | null;
  efficacy_score: number | null;
  efficacy_reviewed_at: string | null;
  ai_review: AiReview | null;
}

export type NotificationType =
  | "bounty_claimed"
  | "work_submitted"
  | "payment_released"
  | "dispute_filed"
  | "dispute_resolved"
  | "bounty_expired"
  | "claim_abandoned"
  | "contract_created"
  | "contract_activated"
  | "contract_cancelled"
  | "snapshot_due"
  | "snapshot_delivered"
  | "snapshot_missed";

export interface Notification {
  id: string;
  user_id: string;
  type: NotificationType;
  title: string;
  message: string;
  reference_id: string | null;
  read: boolean;
  created_at: string;
  read_at: string | null;
}

export interface NotificationListResponse {
  notifications: Notification[];
  total: number;
  unread_count: number;
}

export interface PlatformStats {
  open_bounties: number;
  completed_bounties: number;
  total_settled_ate: number;
  active_agents: number;
}

export type ContractStatus = "draft" | "active" | "paused" | "cancelled" | "completed";

export type SnapshotStatus =
  | "pending"
  | "delivered"
  | "approved"
  | "rejected"
  | "missed"
  | "disputed";

export interface ServiceContract {
  id: string;
  requester_id: string;
  agent_user_id: string;
  agent_exchange_bot_id: string;
  title: string;
  description: string;
  category_id: string | null;
  acceptance_criteria: Record<string, unknown> | null;
  provenance_tier: ProvenanceTier;
  reward_per_snapshot: number;
  schedule: string;
  schedule_description: string;
  max_snapshots: number | null;
  grace_period_hours: number;
  auto_approve: boolean;
  status: ContractStatus;
  group_id: string;
  created_at: string;
  updated_at: string;
  activated_at: string | null;
  cancelled_at: string | null;
  snapshot_count: number;
}

export interface ContractListResponse {
  contracts: ServiceContract[];
  total: number;
}

export interface Snapshot {
  id: string;
  contract_id: string;
  cycle_number: number;
  escrow_id: string | null;
  deliverable: Record<string, unknown> | null;
  provenance: Record<string, unknown> | null;
  status: SnapshotStatus;
  due_at: string;
  deadline_at: string;
  delivered_at: string | null;
  approved_at: string | null;
  reviewer_notes: string | null;
}

export interface SnapshotListResponse {
  snapshots: Snapshot[];
  total: number;
}

export type AssistSessionStatus = "active" | "draft_ready" | "finalized" | "abandoned";

export interface AcceptanceCriteriaAssist {
  description: string;
  output_format: string;
  required_sources: string[] | null;
  provenance_tier: string;
  custom_checks: Record<string, unknown>[] | null;
}

export interface BountyDraft {
  title: string | null;
  description: string | null;
  category_slug: string | null;
  tags: string[] | null;
  acceptance_criteria: AcceptanceCriteriaAssist | null;
  reward_suggestion: number | null;
  difficulty: string | null;
  provenance_tier: string | null;
  deadline_suggestion: string | null;
  settlement_structure: SettlementStructure | null;
}

export interface AssistMessage {
  role: "user" | "assistant";
  content: string;
  timestamp: string;
}

export interface AssistSession {
  id: string;
  status: AssistSessionStatus;
  messages: AssistMessage[];
  bounty_draft: BountyDraft | null;
  settlement_structure: SettlementStructure | null;
  turn_count: number;
  created_at: string;
  updated_at: string;
  finalized_bounty_id: string | null;
}
