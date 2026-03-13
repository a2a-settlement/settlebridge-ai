import type { AgentStatus } from "../../types/gateway";

const STATUS_STYLES: Record<AgentStatus, { bg: string; text: string; dot: string }> = {
  active: { bg: "bg-green-50", text: "text-green-700", dot: "bg-green-500" },
  degraded: { bg: "bg-yellow-50", text: "text-yellow-700", dot: "bg-yellow-500" },
  offline: { bg: "bg-red-50", text: "text-red-700", dot: "bg-red-500" },
};

export default function StatusBadge({ status }: { status: AgentStatus }) {
  const s = STATUS_STYLES[status] ?? STATUS_STYLES.offline;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium ${s.bg} ${s.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
      {status}
    </span>
  );
}
