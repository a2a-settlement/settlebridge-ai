import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  RefreshCw,
  Clock,
  CheckCircle2,
  PauseCircle,
  XCircle,
  Coins,
  CalendarClock,
  Plus,
} from "lucide-react";
import api from "../services/api";
import type { ServiceContract, ContractListResponse, ContractStatus } from "../types";
import { useAuth } from "../hooks/useAuth";

const statusConfig: Record<
  ContractStatus,
  { label: string; color: string; icon: React.ReactNode }
> = {
  draft: { label: "Draft", color: "bg-gray-100 text-gray-600", icon: <Clock className="w-3.5 h-3.5" /> },
  active: { label: "Active", color: "bg-green-100 text-green-700", icon: <RefreshCw className="w-3.5 h-3.5" /> },
  paused: { label: "Paused", color: "bg-yellow-100 text-yellow-700", icon: <PauseCircle className="w-3.5 h-3.5" /> },
  completed: { label: "Completed", color: "bg-blue-100 text-blue-700", icon: <CheckCircle2 className="w-3.5 h-3.5" /> },
  cancelled: { label: "Cancelled", color: "bg-red-100 text-red-600", icon: <XCircle className="w-3.5 h-3.5" /> },
};

function ContractCard({ contract }: { contract: ServiceContract }) {
  const cfg = statusConfig[contract.status];
  return (
    <Link
      to={`/contracts/${contract.id}`}
      className="block bg-white border border-gray-200 rounded-xl p-5 hover:shadow-md transition group"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold text-navy-900 group-hover:text-navy-700 truncate">
            {contract.title}
          </h3>
          <p className="text-sm text-gray-500 mt-1 line-clamp-2">
            {contract.description}
          </p>
        </div>
        <span
          className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full whitespace-nowrap ${cfg.color}`}
        >
          {cfg.icon}
          {cfg.label}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-4 mt-4 text-xs text-gray-500">
        <span className="inline-flex items-center gap-1">
          <Coins className="w-3.5 h-3.5" />
          {contract.reward_per_snapshot} ATE / snapshot
        </span>
        <span className="inline-flex items-center gap-1">
          <CalendarClock className="w-3.5 h-3.5" />
          {contract.schedule_description}
        </span>
        <span>
          {contract.snapshot_count}
          {contract.max_snapshots ? ` / ${contract.max_snapshots}` : ""} snapshots
        </span>
      </div>
    </Link>
  );
}

const FILTER_TABS: { label: string; value: ContractStatus | "all" }[] = [
  { label: "All", value: "all" },
  { label: "Active", value: "active" },
  { label: "Draft", value: "draft" },
  { label: "Paused", value: "paused" },
  { label: "Completed", value: "completed" },
];

export default function ContractList() {
  const { user } = useAuth();
  const [contracts, setContracts] = useState<ServiceContract[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<ContractStatus | "all">("all");

  useEffect(() => {
    const params: Record<string, string> = {};
    if (filter !== "all") params.status = filter;
    api
      .get<ContractListResponse>("/contracts", { params })
      .then(({ data }) => setContracts(data.contracts))
      .finally(() => setLoading(false));
  }, [filter]);

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-navy-900">Service Contracts</h1>
          <p className="text-gray-500 text-sm mt-1">
            Recurring agent services with scheduled deliveries
          </p>
        </div>
        {user && (
          <Link
            to="/contracts/new"
            className="inline-flex items-center gap-2 px-4 py-2 bg-money text-navy-900 rounded-lg text-sm font-semibold hover:bg-money-dark transition"
          >
            <Plus className="w-4 h-4" />
            New Contract
          </Link>
        )}
      </div>

      <div className="flex gap-2 mb-6 overflow-x-auto pb-1">
        {FILTER_TABS.map((tab) => (
          <button
            key={tab.value}
            onClick={() => setFilter(tab.value)}
            className={`px-3 py-1.5 rounded-full text-xs font-medium transition whitespace-nowrap ${
              filter === tab.value
                ? "bg-navy-900 text-white"
                : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="bg-white border rounded-xl p-5 h-28 animate-pulse"
            />
          ))}
        </div>
      ) : contracts.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <RefreshCw className="w-12 h-12 mx-auto mb-3 opacity-50" />
          <p className="font-medium">No contracts found</p>
          <p className="text-sm mt-1">
            {user ? "Create a new service contract to get started." : "Sign in to create contracts."}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {contracts.map((c) => (
            <ContractCard key={c.id} contract={c} />
          ))}
        </div>
      )}
    </div>
  );
}
