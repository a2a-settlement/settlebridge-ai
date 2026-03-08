import { useState } from "react";
import {
  CheckCircle2,
  Clock,
  XCircle,
  AlertTriangle,
  Eye,
  ChevronDown,
  ChevronUp,
  Send,
  ThumbsUp,
  Flag,
} from "lucide-react";
import api from "../services/api";
import type { Snapshot, SnapshotStatus } from "../types";

const statusConfig: Record<
  SnapshotStatus,
  { label: string; color: string; bgColor: string; icon: React.ReactNode }
> = {
  pending: {
    label: "Pending",
    color: "text-blue-600",
    bgColor: "bg-blue-100",
    icon: <Clock className="w-4 h-4" />,
  },
  delivered: {
    label: "Delivered",
    color: "text-purple-600",
    bgColor: "bg-purple-100",
    icon: <Send className="w-4 h-4" />,
  },
  approved: {
    label: "Approved",
    color: "text-green-600",
    bgColor: "bg-green-100",
    icon: <CheckCircle2 className="w-4 h-4" />,
  },
  rejected: {
    label: "Rejected",
    color: "text-red-600",
    bgColor: "bg-red-100",
    icon: <XCircle className="w-4 h-4" />,
  },
  missed: {
    label: "Missed",
    color: "text-gray-500",
    bgColor: "bg-gray-100",
    icon: <AlertTriangle className="w-4 h-4" />,
  },
  disputed: {
    label: "Disputed",
    color: "text-orange-600",
    bgColor: "bg-orange-100",
    icon: <Flag className="w-4 h-4" />,
  },
};

function formatDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function SnapshotItem({
  snapshot,
  prevSnapshot,
  isRequester,
  isAgent,
  contractId,
}: {
  snapshot: Snapshot;
  prevSnapshot: Snapshot | null;
  isRequester: boolean;
  isAgent: boolean;
  contractId: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [approving, setApproving] = useState(false);
  const cfg = statusConfig[snapshot.status];

  const content = snapshot.deliverable?.content as string | undefined;
  const prevContent = prevSnapshot?.deliverable?.content as string | undefined;
  const hasDiff = content && prevContent && content !== prevContent;

  async function handleApprove() {
    setApproving(true);
    try {
      await api.post(`/contracts/snapshots/${snapshot.id}/approve`, { notes: null });
      window.location.reload();
    } catch (err: any) {
      alert(err?.response?.data?.detail || "Approve failed");
    } finally {
      setApproving(false);
    }
  }

  return (
    <div className="relative pl-8">
      <div
        className={`absolute left-0 top-1 w-6 h-6 rounded-full flex items-center justify-center ${cfg.bgColor} ${cfg.color}`}
      >
        {cfg.icon}
      </div>

      <div className="bg-gray-50 rounded-lg p-4 mb-4">
        <div className="flex items-center justify-between gap-3 mb-1">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-navy-900 text-sm">
              Cycle {snapshot.cycle_number}
            </span>
            <span
              className={`text-xs font-medium px-2 py-0.5 rounded-full ${cfg.bgColor} ${cfg.color}`}
            >
              {cfg.label}
            </span>
          </div>
          <span className="text-xs text-gray-400">Due {formatDate(snapshot.due_at)}</span>
        </div>

        {snapshot.delivered_at && (
          <p className="text-xs text-gray-500 mb-2">
            Delivered {formatDate(snapshot.delivered_at)}
            {snapshot.approved_at && <> · Approved {formatDate(snapshot.approved_at)}</>}
          </p>
        )}

        {snapshot.reviewer_notes && (
          <p className="text-xs text-gray-500 bg-white rounded px-2 py-1 mb-2 italic">
            {snapshot.reviewer_notes}
          </p>
        )}

        {content && (
          <>
            <button
              onClick={() => setExpanded(!expanded)}
              className="inline-flex items-center gap-1 text-xs text-navy-600 hover:text-navy-800 transition"
            >
              <Eye className="w-3.5 h-3.5" />
              {expanded ? "Hide" : "View"} deliverable
              {expanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            </button>

            {expanded && (
              <div className="mt-2 space-y-2">
                <pre className="bg-white border rounded-lg p-3 text-xs text-gray-700 overflow-x-auto max-h-64 whitespace-pre-wrap">
                  {content}
                </pre>
                {hasDiff && (
                  <details className="text-xs">
                    <summary className="text-navy-600 cursor-pointer hover:text-navy-800">
                      Show diff from previous cycle
                    </summary>
                    <pre className="mt-1 bg-white border rounded-lg p-3 text-gray-500 overflow-x-auto max-h-48 whitespace-pre-wrap">
                      {prevContent}
                    </pre>
                  </details>
                )}
              </div>
            )}
          </>
        )}

        {isRequester && snapshot.status === "delivered" && (
          <div className="flex gap-2 mt-3">
            <button
              disabled={approving}
              onClick={handleApprove}
              className="inline-flex items-center gap-1 px-3 py-1.5 bg-green-600 text-white rounded-lg text-xs font-semibold hover:bg-green-700 transition disabled:opacity-50"
            >
              <ThumbsUp className="w-3.5 h-3.5" /> Approve
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export default function SnapshotTimeline({
  snapshots,
  contractId,
  isRequester,
  isAgent,
}: {
  snapshots: Snapshot[];
  contractId: string;
  isRequester: boolean;
  isAgent: boolean;
}) {
  const sorted = [...snapshots].sort((a, b) => b.cycle_number - a.cycle_number);

  return (
    <div className="relative">
      <div className="absolute left-3 top-0 bottom-0 w-px bg-gray-200" />
      {sorted.map((snap, idx) => {
        const prevIdx = idx + 1;
        const prevSnap = prevIdx < sorted.length ? sorted[prevIdx] : null;
        return (
          <SnapshotItem
            key={snap.id}
            snapshot={snap}
            prevSnapshot={prevSnap}
            isRequester={isRequester}
            isAgent={isAgent}
            contractId={contractId}
          />
        );
      })}
    </div>
  );
}
