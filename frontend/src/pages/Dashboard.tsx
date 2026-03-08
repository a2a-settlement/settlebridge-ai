import { useEffect, useState } from "react";
import { Link, Routes, Route } from "react-router-dom";
import {
  FileText,
  ClipboardList,
  Bell,
  Settings,
  Coins,
  TrendingUp,
  RefreshCw,
} from "lucide-react";
import { useAuth } from "../hooks/useAuth";
import api from "../services/api";
import type { Bounty, Claim, BountyListResponse } from "../types";
import BountyCard from "../components/BountyCard";

interface EfficacyItem {
  id: string;
  bounty_id: string;
  score: number | null;
  release_percent: number | null;
  efficacy_check_at: string | null;
  efficacy_criteria: string | null;
  bounty?: Bounty;
}

function EfficacyReviewsDue() {
  const [items, setItems] = useState<EfficacyItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<Bounty[]>("/bounties/my/posted")
      .then(async ({ data: bounties }) => {
        const due: EfficacyItem[] = [];
        for (const b of bounties) {
          if (!["CLAIMED", "IN_REVIEW", "SUBMITTED"].includes(b.status)) continue;
          try {
            const { data: subs } = await api.get(`/bounties/${b.id}/submissions`);
            for (const sub of subs as any[]) {
              if (sub.status === "partially_approved") {
                due.push({ ...sub, bounty: b });
              }
            }
          } catch {}
        }
        setItems(due);
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading || items.length === 0) return null;

  return (
    <div className="mb-8">
      <h2 className="text-lg font-semibold text-navy-900 mb-3 flex items-center gap-2">
        <TrendingUp className="w-5 h-5 text-amber-500" />
        Efficacy Reviews Due
      </h2>
      <div className="space-y-3">
        {items.map((item) => (
          <Link
            key={item.id}
            to={`/dashboard/submissions/${item.id}`}
            className="block bg-amber-50 border border-amber-200 rounded-xl p-4 hover:bg-amber-100 transition"
          >
            <div className="flex items-center justify-between">
              <div>
                <p className="font-medium text-navy-900 text-sm">
                  {item.bounty?.title || `Bounty ${item.bounty_id.slice(0, 8)}...`}
                </p>
                <p className="text-xs text-amber-700 mt-0.5">
                  {item.release_percent}% released (score: {item.score}) &middot;
                  Holdback: {100 - (item.release_percent || 0)}%
                </p>
                {item.efficacy_criteria && (
                  <p className="text-xs text-amber-600 mt-0.5">
                    Criteria: {item.efficacy_criteria}
                  </p>
                )}
              </div>
              <div className="text-right flex-shrink-0 ml-4">
                {item.efficacy_check_at && (
                  <p className="text-xs text-amber-700">
                    Due: {new Date(item.efficacy_check_at).toLocaleDateString()}
                  </p>
                )}
                <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-amber-200 text-amber-800">
                  Review
                </span>
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

function RequesterDashboard() {
  const [bounties, setBounties] = useState<Bounty[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<Bounty[]>("/bounties/my/posted")
      .then(({ data }) => setBounties(data))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <EfficacyReviewsDue />

      <div className="flex items-center justify-between mb-6">
        <h2 className="text-lg font-semibold text-navy-900">My Bounties</h2>
        <Link
          to="/bounties/new"
          className="px-4 py-2 bg-money text-navy-900 rounded-lg text-sm font-semibold hover:bg-money-dark transition"
        >
          Post New Bounty
        </Link>
      </div>
      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="bg-white border rounded-xl p-5 h-32 animate-pulse"
            />
          ))}
        </div>
      ) : bounties.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <FileText className="w-12 h-12 mx-auto mb-3 opacity-50" />
          <p className="font-medium">No bounties yet</p>
          <p className="text-sm mt-1">Post your first bounty to get started</p>
        </div>
      ) : (
        <div className="space-y-3">
          {bounties.map((b) => (
            <BountyCard key={b.id} bounty={b} />
          ))}
        </div>
      )}
    </div>
  );
}

function AgentDashboard() {
  const [claims, setClaims] = useState<Claim[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<Claim[]>("/bounties/my/claimed")
      .then(({ data }) => setClaims(data))
      .finally(() => setLoading(false));
  }, []);

  const statusColors: Record<string, string> = {
    active: "bg-blue-100 text-blue-700",
    submitted: "bg-purple-100 text-purple-700",
    accepted: "bg-green-100 text-green-800",
    rejected: "bg-red-100 text-red-700",
    abandoned: "bg-gray-100 text-gray-500",
  };

  return (
    <div>
      <h2 className="text-lg font-semibold text-navy-900 mb-6">My Claims</h2>
      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="bg-white border rounded-xl p-5 h-20 animate-pulse"
            />
          ))}
        </div>
      ) : claims.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <ClipboardList className="w-12 h-12 mx-auto mb-3 opacity-50" />
          <p className="font-medium">No claims yet</p>
          <p className="text-sm mt-1">
            <Link to="/bounties" className="text-navy-600 hover:underline">
              Browse bounties
            </Link>{" "}
            to find work
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {claims.map((c) => (
            <div
              key={c.id}
              className="bg-white border border-gray-200 rounded-xl p-4 flex items-center justify-between"
            >
              <div>
                <Link
                  to={`/bounties/${c.bounty_id}`}
                  className="font-medium text-navy-900 hover:underline text-sm"
                >
                  Bounty {c.bounty_id.slice(0, 8)}...
                </Link>
                <p className="text-xs text-gray-500 mt-0.5">
                  Claimed {new Date(c.claimed_at).toLocaleDateString()}
                </p>
              </div>
              <div className="flex items-center gap-3">
                <span
                  className={`text-xs font-medium px-2.5 py-1 rounded-full ${statusColors[c.status] || "bg-gray-100"}`}
                >
                  {c.status}
                </span>
                {c.status === "active" && (
                  <Link
                    to={`/dashboard/claims/${c.id}/submit`}
                    className="px-3 py-1.5 bg-navy-900 text-white rounded-lg text-xs font-semibold hover:bg-navy-800 transition"
                  >
                    Submit Work
                  </Link>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Dashboard() {
  const { user } = useAuth();
  const [tab, setTab] = useState<"bounties" | "claims">("bounties");

  if (!user) return null;

  const isRequester =
    user.user_type === "requester" || user.user_type === "both";
  const isAgent =
    user.user_type === "agent_operator" || user.user_type === "both";

  const defaultTab = isRequester ? "bounties" : "claims";
  const activeTab = isRequester && isAgent ? tab : defaultTab;

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-navy-900">Dashboard</h1>
        <p className="text-gray-500 text-sm mt-1">
          Welcome back, {user.display_name}
        </p>
      </div>

      <div className="flex gap-8">
        <aside className="hidden lg:block w-48 flex-shrink-0">
          <nav className="space-y-1">
            {isRequester && (
              <button
                onClick={() => setTab("bounties")}
                className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition ${
                  activeTab === "bounties"
                    ? "bg-navy-900 text-white font-medium"
                    : "text-gray-700 hover:bg-gray-100"
                }`}
              >
                <FileText className="w-4 h-4" /> My Bounties
              </button>
            )}
            {isAgent && (
              <button
                onClick={() => setTab("claims")}
                className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition ${
                  activeTab === "claims"
                    ? "bg-navy-900 text-white font-medium"
                    : "text-gray-700 hover:bg-gray-100"
                }`}
              >
                <ClipboardList className="w-4 h-4" /> My Claims
              </button>
            )}
            <Link
              to="/contracts"
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-gray-700 hover:bg-gray-100"
            >
              <RefreshCw className="w-4 h-4" /> My Contracts
            </Link>
            <Link
              to="/dashboard/notifications"
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-gray-700 hover:bg-gray-100"
            >
              <Bell className="w-4 h-4" /> Notifications
            </Link>
            <Link
              to="/dashboard/settings"
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-gray-700 hover:bg-gray-100"
            >
              <Settings className="w-4 h-4" /> Settings
            </Link>
          </nav>
        </aside>

        <div className="flex-1 min-w-0">
          {/* Mobile tab bar */}
          {isRequester && isAgent && (
            <div className="lg:hidden flex gap-2 mb-6">
              <button
                onClick={() => setTab("bounties")}
                className={`flex-1 py-2.5 rounded-lg text-sm font-medium transition ${
                  activeTab === "bounties"
                    ? "bg-navy-900 text-white"
                    : "bg-gray-100 text-gray-600"
                }`}
              >
                My Bounties
              </button>
              <button
                onClick={() => setTab("claims")}
                className={`flex-1 py-2.5 rounded-lg text-sm font-medium transition ${
                  activeTab === "claims"
                    ? "bg-navy-900 text-white"
                    : "bg-gray-100 text-gray-600"
                }`}
              >
                My Claims
              </button>
            </div>
          )}

          {activeTab === "bounties" && isRequester && <RequesterDashboard />}
          {activeTab === "claims" && isAgent && <AgentDashboard />}
        </div>
      </div>
    </div>
  );
}
