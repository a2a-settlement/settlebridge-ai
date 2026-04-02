import { useEffect, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Coins,
  Clock,
  Tag,
  User as UserIcon,
  AlertCircle,
  CheckCircle,
  XCircle,
  FileText,
  AlertTriangle,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import api from "../services/api";
import type { Bounty, Claim } from "../types";
import { useAuth } from "../hooks/useAuth";
import ProvenanceBadge from "../components/ProvenanceBadge";
import DeliverableViewer from "../components/DeliverableViewer";

interface Submission {
  id: string;
  claim_id: string;
  bounty_id: string;
  agent_user_id: string;
  deliverable: { content: string; content_type?: string };
  provenance: Record<string, any> | null;
  status: string;
  reviewer_notes: string | null;
  submitted_at: string;
  reviewed_at: string | null;
  score: number | null;
  release_percent: number | null;
  efficacy_check_at: string | null;
  efficacy_criteria: string | null;
  efficacy_score: number | null;
  efficacy_reviewed_at: string | null;
}

export default function BountyDetail() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [bounty, setBounty] = useState<Bounty | null>(null);
  const [loading, setLoading] = useState(true);
  const [claiming, setClaiming] = useState(false);
  const [funding, setFunding] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [submissions, setSubmissions] = useState<Submission[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .get<Bounty>(`/bounties/${id}`)
      .then(({ data }) => {
        setBounty(data);
        if (["in_review", "submitted", "completed", "disputed"].includes(data.status)) {
          api.get<Submission[]>(`/bounties/${id}/submissions`).then(({ data: subs }) => {
            setSubmissions(subs);
          }).catch(() => {});
        }
      })
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

  const handleFund = async () => {
    if (!bounty) return;
    setFunding(true);
    setError("");
    try {
      const { data } = await api.post<Bounty>(`/bounties/${bounty.id}/fund`);
      setBounty(data);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to fund bounty");
    } finally {
      setFunding(false);
    }
  };

  const handleApprove = async (submissionId: string) => {
    setReviewing(true);
    setError("");
    try {
      await api.post(`/submissions/${submissionId}/approve`);
      const { data } = await api.get<Bounty>(`/bounties/${id}`);
      setBounty(data);
      const { data: subs } = await api.get<Submission[]>(`/bounties/${id}/submissions`);
      setSubmissions(subs);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to approve submission");
    } finally {
      setReviewing(false);
    }
  };

  const handleReject = async (submissionId: string) => {
    setReviewing(true);
    setError("");
    try {
      await api.post(`/submissions/${submissionId}/reject`);
      const { data } = await api.get<Bounty>(`/bounties/${id}`);
      setBounty(data);
      const { data: subs } = await api.get<Submission[]>(`/bounties/${id}/submissions`);
      setSubmissions(subs);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to reject submission");
    } finally {
      setReviewing(false);
    }
  };

  const handleCancel = async () => {
    if (!bounty) return;
    setCancelling(true);
    setError("");
    try {
      const { data } = await api.post<Bounty>(`/bounties/${bounty.id}/cancel`);
      setBounty(data);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to cancel bounty");
    } finally {
      setCancelling(false);
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

  const statusColors: Record<string, string> = {
    draft: "bg-gray-100 text-gray-600",
    open: "bg-money/10 text-money-dark",
    claimed: "bg-blue-100 text-blue-700",
    submitted: "bg-purple-100 text-purple-700",
    in_review: "bg-indigo-100 text-indigo-700",
    completed: "bg-green-100 text-green-800",
    disputed: "bg-red-100 text-red-700",
    expired: "bg-gray-100 text-gray-500",
    cancelled: "bg-gray-100 text-gray-500",
  };

  const isOwner = user && user.id === bounty.requester_id;

  const canClaim =
    user &&
    !isOwner &&
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

      {isOwner && bounty.status === "draft" && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 mb-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
          <div className="text-sm">
            <p className="font-semibold text-amber-800">This bounty is a draft</p>
            <p className="text-amber-700 mt-0.5">
              It's only visible to you. Click <strong>Fund &amp; Publish</strong> below to make it live on the marketplace.
            </p>
          </div>
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
                <span className={`text-xs font-medium px-2.5 py-1 rounded-full capitalize ${statusColors[bounty.status] ?? "bg-gray-100 text-gray-600"}`}>
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

          {submissions.length > 0 && (
            <div className="border-t pt-6 mb-6">
              <h3 className="font-semibold text-navy-900 text-sm mb-4 flex items-center gap-2">
                <FileText className="w-4 h-4" />
                Submissions ({submissions.length})
              </h3>
              <div className="space-y-4">
                {submissions.map((sub) => (
                  <div key={sub.id} className="bg-gray-50 rounded-xl p-5">
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2">
                        <span className={`text-xs font-medium px-2.5 py-1 rounded-full ${
                          sub.status === "approved" ? "bg-green-100 text-green-800" :
                          sub.status === "rejected" ? "bg-red-100 text-red-700" :
                          sub.status === "disputed" ? "bg-red-100 text-red-700" :
                          sub.status === "partially_approved" ? "bg-amber-100 text-amber-700" :
                          "bg-indigo-100 text-indigo-700"
                        }`}>
                          {sub.status.replace("_", " ")}
                        </span>
                        <span className="text-xs text-gray-500">
                          {new Date(sub.submitted_at).toLocaleString()}
                        </span>
                      </div>
                      {isOwner && sub.status === "pending_review" && (
                        <Link
                          to={`/dashboard/submissions/${sub.id}`}
                          className="flex items-center gap-1.5 px-4 py-2 bg-navy-900 text-white rounded-lg text-xs font-semibold hover:bg-navy-800 transition"
                        >
                          <CheckCircle className="w-3.5 h-3.5" /> Review & Score
                        </Link>
                      )}
                      {isOwner && sub.status === "partially_approved" && (
                        <Link
                          to={`/dashboard/submissions/${sub.id}`}
                          className="flex items-center gap-1.5 px-4 py-2 bg-amber-600 text-white rounded-lg text-xs font-semibold hover:bg-amber-700 transition"
                        >
                          <AlertTriangle className="w-3.5 h-3.5" /> Efficacy Review
                        </Link>
                      )}
                    </div>
                    <div className="text-gray-700 bg-white rounded-lg p-4 border border-gray-200">
                      {sub.deliverable.content_type?.includes("markdown") ? (
                        <div className="prose prose-sm max-w-none">
                          <ReactMarkdown>{sub.deliverable.content}</ReactMarkdown>
                        </div>
                      ) : (
                        <DeliverableViewer
                          content={sub.deliverable.content}
                          contentType={sub.deliverable.content_type || "text/plain"}
                          bountyTitle={bounty.title}
                        />
                      )}
                    </div>
                    {sub.provenance && (
                      <div className="mt-3 text-xs text-gray-500">
                        <span className="font-medium">Provenance:</span>{" "}
                        {sub.provenance.source_refs?.join(", ") || sub.provenance.attestation_level || "self-declared"}
                        {sub.provenance.content_hash && (
                          <span className="ml-2 font-mono">[hash: {sub.provenance.content_hash.slice(0, 12)}...]</span>
                        )}
                      </div>
                    )}
                    {sub.reviewer_notes && (
                      <div className="mt-2 text-xs text-gray-600 italic">
                        Review notes: {sub.reviewer_notes}
                      </div>
                    )}
                  </div>
                ))}
              </div>
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
            <div className="flex items-center gap-3">
              {isOwner && bounty.status === "draft" && (
                <>
                  <button
                    onClick={handleCancel}
                    disabled={cancelling}
                    className="px-5 py-2.5 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition disabled:opacity-50"
                  >
                    {cancelling ? "Cancelling..." : "Cancel"}
                  </button>
                  <button
                    onClick={handleFund}
                    disabled={funding}
                    className="px-6 py-2.5 bg-money text-navy-900 rounded-lg font-bold text-sm hover:bg-money-dark transition disabled:opacity-50"
                  >
                    {funding ? "Publishing..." : "Fund & Publish"}
                  </button>
                </>
              )}
              {isOwner && bounty.status === "open" && (
                <button
                  onClick={handleCancel}
                  disabled={cancelling}
                  className="px-5 py-2.5 border border-red-300 text-red-700 rounded-lg text-sm font-medium hover:bg-red-50 transition disabled:opacity-50"
                >
                  {cancelling ? "Cancelling..." : "Cancel Bounty"}
                </button>
              )}
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
    </div>
  );
}
