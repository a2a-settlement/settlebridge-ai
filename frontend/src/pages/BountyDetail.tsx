import { useEffect, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Coins,
  Clock,
  Tag,
  User as UserIcon,
  AlertCircle,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import api from "../services/api";
import type { Bounty, Claim } from "../types";
import { useAuth } from "../hooks/useAuth";
import ProvenanceBadge from "../components/ProvenanceBadge";

export default function BountyDetail() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [bounty, setBounty] = useState<Bounty | null>(null);
  const [loading, setLoading] = useState(true);
  const [claiming, setClaiming] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .get<Bounty>(`/bounties/${id}`)
      .then(({ data }) => setBounty(data))
      .catch(() => setError("Bounty not found"))
      .finally(() => setLoading(false));
  }, [id]);

  const handleClaim = async () => {
    if (!bounty) return;
    setClaiming(true);
    setError("");
    try {
      const { data } = await api.post<Claim>(`/bounties/${bounty.id}/claim`);
      navigate(`/dashboard/claims/${data.id}/submit`);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to claim bounty");
    } finally {
      setClaiming(false);
    }
  };

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-8 animate-pulse">
        <div className="h-8 bg-gray-200 rounded w-2/3 mb-4" />
        <div className="h-4 bg-gray-200 rounded w-full mb-2" />
        <div className="h-4 bg-gray-200 rounded w-5/6" />
      </div>
    );
  }

  if (!bounty) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-20 text-center text-gray-500">
        Bounty not found
      </div>
    );
  }

  const canClaim =
    user &&
    (user.user_type === "agent_operator" || user.user_type === "both") &&
    bounty.status === "open" &&
    user.exchange_bot_id;

  const ac = bounty.acceptance_criteria as Record<string, any> | null;

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <Link
        to="/bounties"
        className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-navy-700 mb-6"
      >
        <ArrowLeft className="w-4 h-4" /> Back to bounties
      </Link>

      {error && (
        <div className="bg-red-50 text-red-700 rounded-lg p-3 mb-4 flex items-center gap-2 text-sm">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      <div className="bg-white border border-gray-200 rounded-2xl overflow-hidden">
        <div className="p-6 sm:p-8">
          <div className="flex flex-wrap items-start justify-between gap-4 mb-6">
            <div className="flex-1 min-w-0">
              <h1 className="text-2xl font-bold text-navy-900 mb-3">
                {bounty.title}
              </h1>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-money/10 text-money-dark capitalize">
                  {bounty.status.replace("_", " ")}
                </span>
                <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-yellow-100 text-yellow-800 capitalize">
                  {bounty.difficulty}
                </span>
                <ProvenanceBadge tier={bounty.provenance_tier} />
                {bounty.category && (
                  <span className="text-xs text-gray-500 flex items-center gap-1">
                    <Tag className="w-3 h-3" /> {bounty.category.name}
                  </span>
                )}
              </div>
            </div>
            <div className="text-right">
              <div className="flex items-center gap-2 text-2xl font-bold text-money-dark">
                <Coins className="w-6 h-6" />
                {bounty.reward_amount} ATE
              </div>
              {bounty.deadline && (
                <p className="text-xs text-gray-500 mt-1 flex items-center gap-1 justify-end">
                  <Clock className="w-3 h-3" />
                  Due {new Date(bounty.deadline).toLocaleDateString()}
                </p>
              )}
            </div>
          </div>

          <div className="prose prose-sm max-w-none text-gray-700 mb-8">
            <ReactMarkdown>{bounty.description}</ReactMarkdown>
          </div>

          {ac && (
            <div className="bg-gray-50 rounded-xl p-5 mb-6">
              <h3 className="font-semibold text-navy-900 text-sm mb-3">
                Acceptance Criteria
              </h3>
              {ac.description && (
                <p className="text-sm text-gray-700 mb-2">{ac.description}</p>
              )}
              <div className="grid sm:grid-cols-2 gap-3 text-sm">
                {ac.output_format && (
                  <div>
                    <span className="text-gray-500">Output Format:</span>{" "}
                    <span className="font-medium">{ac.output_format}</span>
                  </div>
                )}
                {ac.provenance_tier && (
                  <div>
                    <span className="text-gray-500">Provenance:</span>{" "}
                    <span className="font-medium">
                      {(ac.provenance_tier as string).replace(/_/g, " ")}
                    </span>
                  </div>
                )}
                {ac.required_sources && (
                  <div className="sm:col-span-2">
                    <span className="text-gray-500">Required Sources:</span>{" "}
                    <span className="font-medium">
                      {(ac.required_sources as string[]).join(", ")}
                    </span>
                  </div>
                )}
              </div>
            </div>
          )}

          {bounty.tags && bounty.tags.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-6">
              {bounty.tags.map((tag) => (
                <span
                  key={tag}
                  className="px-3 py-1 bg-gray-100 text-gray-600 rounded-full text-xs font-medium"
                >
                  {tag}
                </span>
              ))}
            </div>
          )}

          <div className="border-t pt-6 flex items-center justify-between">
            <div className="text-sm text-gray-500">
              <span>Max claims: {bounty.max_claims}</span>
              {bounty.min_reputation != null && (
                <span className="ml-4">
                  Min reputation: {Math.round(bounty.min_reputation * 100)}%
                </span>
              )}
              {bounty.auto_approve && (
                <span className="ml-4 text-money-dark font-medium">
                  Auto-Approve enabled
                </span>
              )}
            </div>
            {canClaim && (
              <button
                onClick={handleClaim}
                disabled={claiming}
                className="px-6 py-2.5 bg-navy-900 text-white rounded-lg font-semibold text-sm hover:bg-navy-800 transition disabled:opacity-50"
              >
                {claiming ? "Claiming..." : "Claim Bounty"}
              </button>
            )}
            {!user && (
              <Link
                to="/login"
                className="px-6 py-2.5 bg-navy-900 text-white rounded-lg font-semibold text-sm hover:bg-navy-800 transition"
              >
                Sign in to Claim
              </Link>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
