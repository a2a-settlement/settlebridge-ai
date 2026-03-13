import type { PolicyDecision } from "../../types/gateway";

const DECISION_STYLES: Record<PolicyDecision, { bg: string; text: string }> = {
  approve: { bg: "bg-green-50", text: "text-green-700" },
  block: { bg: "bg-red-50", text: "text-red-700" },
  flag: { bg: "bg-yellow-50", text: "text-yellow-700" },
};

export default function PolicyDecisionBadge({ decision }: { decision: PolicyDecision }) {
  const s = DECISION_STYLES[decision] ?? DECISION_STYLES.block;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${s.bg} ${s.text}`}>
      {decision}
    </span>
  );
}
