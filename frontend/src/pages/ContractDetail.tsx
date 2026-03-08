import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Play,
  Pause,
  X,
  RotateCcw,
  Coins,
  CalendarClock,
  Shield,
  Clock,
} from "lucide-react";
import api from "../services/api";
import type { ServiceContract, Snapshot, SnapshotListResponse } from "../types";
import { useAuth } from "../hooks/useAuth";
import SnapshotTimeline from "../components/SnapshotTimeline";

export default function ContractDetail() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [contract, setContract] = useState<ServiceContract | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);

  useEffect(() => {
    if (!id) return;
    Promise.all([
      api.get<ServiceContract>(`/contracts/${id}`),
      api.get<SnapshotListResponse>(`/contracts/${id}/snapshots`),
    ])
      .then(([contractRes, snapRes]) => {
        setContract(contractRes.data);
        setSnapshots(snapRes.data.snapshots);
      })
      .catch(() => navigate("/contracts"))
      .finally(() => setLoading(false));
  }, [id, navigate]);

  async function doAction(action: string) {
    if (!id) return;
    setActionLoading(true);
    try {
      const { data } = await api.post<ServiceContract>(`/contracts/${id}/${action}`);
      setContract(data);
    } catch (err: any) {
      alert(err?.response?.data?.detail || "Action failed");
    } finally {
      setActionLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-8">
        <div className="animate-pulse space-y-4">
          <div className="h-8 w-64 bg-gray-200 rounded" />
          <div className="h-40 bg-gray-100 rounded-xl" />
          <div className="h-64 bg-gray-100 rounded-xl" />
        </div>
      </div>
    );
  }

  if (!contract) return null;

  const isRequester = user?.id === contract.requester_id;
  const isAgent = user?.id === contract.agent_user_id;

  const statusColors: Record<string, string> = {
    draft: "bg-gray-100 text-gray-600",
    active: "bg-green-100 text-green-700",
    paused: "bg-yellow-100 text-yellow-700",
    completed: "bg-blue-100 text-blue-700",
    cancelled: "bg-red-100 text-red-600",
  };

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <button
        onClick={() => navigate("/contracts")}
        className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-navy-900 mb-6 transition"
      >
        <ArrowLeft className="w-4 h-4" /> Back to Contracts
      </button>

      <div className="bg-white border border-gray-200 rounded-xl p-6 mb-6">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <h1 className="text-xl font-bold text-navy-900">{contract.title}</h1>
            <p className="text-gray-500 mt-1">{contract.description}</p>
          </div>
          <span
            className={`text-xs font-medium px-3 py-1.5 rounded-full capitalize ${statusColors[contract.status] || "bg-gray-100"}`}
          >
            {contract.status}
          </span>
        </div>

        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          <div className="bg-gray-50 rounded-lg p-3">
            <div className="flex items-center gap-1.5 text-xs text-gray-500 mb-1">
              <Coins className="w-3.5 h-3.5" /> Reward
            </div>
            <p className="text-lg font-bold text-navy-900">
              {contract.reward_per_snapshot} <span className="text-xs font-normal text-gray-400">ATE</span>
            </p>
          </div>
          <div className="bg-gray-50 rounded-lg p-3">
            <div className="flex items-center gap-1.5 text-xs text-gray-500 mb-1">
              <CalendarClock className="w-3.5 h-3.5" /> Schedule
            </div>
            <p className="text-sm font-semibold text-navy-900">{contract.schedule_description}</p>
          </div>
          <div className="bg-gray-50 rounded-lg p-3">
            <div className="flex items-center gap-1.5 text-xs text-gray-500 mb-1">
              <Shield className="w-3.5 h-3.5" /> Provenance
            </div>
            <p className="text-sm font-semibold text-navy-900 capitalize">
              {contract.provenance_tier.replace("_", " ")}
            </p>
          </div>
          <div className="bg-gray-50 rounded-lg p-3">
            <div className="flex items-center gap-1.5 text-xs text-gray-500 mb-1">
              <Clock className="w-3.5 h-3.5" /> Progress
            </div>
            <p className="text-lg font-bold text-navy-900">
              {contract.snapshot_count}
              {contract.max_snapshots ? ` / ${contract.max_snapshots}` : ""}
            </p>
          </div>
        </div>

        {isRequester && (
          <div className="flex flex-wrap gap-2 border-t pt-4">
            {contract.status === "draft" && (
              <button
                disabled={actionLoading}
                onClick={() => doAction("activate")}
                className="inline-flex items-center gap-1.5 px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-semibold hover:bg-green-700 transition disabled:opacity-50"
              >
                <Play className="w-4 h-4" /> Activate
              </button>
            )}
            {contract.status === "active" && (
              <button
                disabled={actionLoading}
                onClick={() => doAction("pause")}
                className="inline-flex items-center gap-1.5 px-4 py-2 bg-yellow-500 text-white rounded-lg text-sm font-semibold hover:bg-yellow-600 transition disabled:opacity-50"
              >
                <Pause className="w-4 h-4" /> Pause
              </button>
            )}
            {contract.status === "paused" && (
              <button
                disabled={actionLoading}
                onClick={() => doAction("resume")}
                className="inline-flex items-center gap-1.5 px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-semibold hover:bg-green-700 transition disabled:opacity-50"
              >
                <RotateCcw className="w-4 h-4" /> Resume
              </button>
            )}
            {!["cancelled", "completed"].includes(contract.status) && (
              <button
                disabled={actionLoading}
                onClick={() => {
                  if (confirm("Cancel this contract? This cannot be undone.")) doAction("cancel");
                }}
                className="inline-flex items-center gap-1.5 px-4 py-2 bg-red-600 text-white rounded-lg text-sm font-semibold hover:bg-red-700 transition disabled:opacity-50"
              >
                <X className="w-4 h-4" /> Cancel
              </button>
            )}
          </div>
        )}
      </div>

      <div className="bg-white border border-gray-200 rounded-xl p-6">
        <h2 className="text-lg font-semibold text-navy-900 mb-4">Snapshot History</h2>
        {snapshots.length === 0 ? (
          <p className="text-gray-400 text-sm text-center py-8">
            No snapshots yet. {contract.status === "draft" ? "Activate the contract to start the schedule." : "Snapshots will appear as cycles complete."}
          </p>
        ) : (
          <SnapshotTimeline
            snapshots={snapshots}
            contractId={contract.id}
            isRequester={isRequester}
            isAgent={isAgent}
          />
        )}
      </div>
    </div>
  );
}
