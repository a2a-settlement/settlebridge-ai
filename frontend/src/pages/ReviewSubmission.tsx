import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Star,
  Clock,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import api from "../services/api";
import type { Submission, Bounty } from "../types";
import ProvenanceBadge from "../components/ProvenanceBadge";

const SCORE_LABELS: [number, string, string][] = [
  [0, "Unacceptable", "text-red-600"],
  [25, "Poor", "text-red-500"],
  [50, "Fair", "text-amber-600"],
  [75, "Good", "text-green-600"],
  [90, "Excellent", "text-emerald-600"],
];

function scoreLabel(s: number): [string, string] {
  for (let i = SCORE_LABELS.length - 1; i >= 0; i--) {
    if (s >= SCORE_LABELS[i][0]) return [SCORE_LABELS[i][1], SCORE_LABELS[i][2]];
  }
  return ["", ""];
}

export default function ReviewSubmission() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [bounty, setBounty] = useState<Bounty | null>(null);
  const [notes, setNotes] = useState("");
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState("");

  const [score, setScore] = useState(100);
  const [releasePercent, setReleasePercent] = useState(100);
  const [useHoldback, setUseHoldback] = useState(false);
  const [checkDays, setCheckDays] = useState(3);
  const [criteria, setCriteria] = useState("");

  const [efficacyScore, setEfficacyScore] = useState(75);

  useEffect(() => {
    api
      .get<Submission>(`/submissions/${id}`)
      .then(async ({ data }) => {
        setSubmission(data);
        const b = await api.get<Bounty>(`/bounties/${data.bounty_id}`);
        setBounty(b.data);
      })
      .catch(() => setError("Submission not found"))
      .finally(() => setLoading(false));
  }, [id]);

  const handleApprove = async () => {
    if (!submission) return;
    setActing(true);
    setError("");
    try {
      const body: Record<string, unknown> = {
        score,
        release_percent: useHoldback ? releasePercent : 100,
        notes: notes || undefined,
      };
      if (useHoldback && releasePercent < 100) {
        const checkDate = new Date();
        checkDate.setDate(checkDate.getDate() + checkDays);
        body.efficacy_check_at = checkDate.toISOString();
        body.efficacy_criteria = criteria || undefined;
      }
      await api.post(`/submissions/${submission.id}/approve`, body);
      navigate("/dashboard");
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to approve");
    } finally {
      setActing(false);
    }
  };

  const handleReject = async () => {
    if (!submission) return;
    setActing(true);
    setError("");
    try {
      await api.post(`/submissions/${submission.id}/reject`, { notes });
      navigate("/dashboard");
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to reject");
    } finally {
      setActing(false);
    }
  };

  const handleDispute = async () => {
    if (!submission) return;
    setActing(true);
    setError("");
    try {
      await api.post(`/submissions/${submission.id}/dispute`, {
        reason: notes || "Disputed",
      });
      navigate("/dashboard");
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to dispute");
    } finally {
      setActing(false);
    }
  };

  const handleEfficacyReview = async (action: "release" | "refund") => {
    if (!submission) return;
    setActing(true);
    setError("");
    try {
      await api.post(`/submissions/${submission.id}/efficacy-review`, {
        score: efficacyScore,
        action,
        notes: notes || undefined,
      });
      navigate("/dashboard");
    } catch (err: any) {
      setError(err.response?.data?.detail || `Failed to ${action} holdback`);
    } finally {
      setActing(false);
    }
  };

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-8 animate-pulse">
        <div className="h-8 bg-gray-200 rounded w-1/2 mb-4" />
        <div className="h-64 bg-gray-200 rounded" />
      </div>
    );
  }

  if (!submission || !bounty) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-20 text-center text-gray-500">
        Submission not found
      </div>
    );
  }

  const deliverable = submission.deliverable as Record<string, any>;
  const provenance = submission.provenance as Record<string, any> | null;
  const [label, labelColor] = scoreLabel(score);
  const isPending = submission.status === "pending_review";
  const isPartial = submission.status === "partially_approved";

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <button
        onClick={() => navigate(-1)}
        className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-navy-700 mb-6"
      >
        <ArrowLeft className="w-4 h-4" /> Back
      </button>

      <h1 className="text-2xl font-bold text-navy-900 mb-2">
        {isPartial ? "Efficacy Review" : "Review Submission"}
      </h1>
      <p className="text-gray-500 text-sm mb-6">
        For bounty: <strong>{bounty.title}</strong>
        {bounty.reward_amount && (
          <span className="ml-2 text-money font-semibold">
            {bounty.reward_amount} ATE
          </span>
        )}
      </p>

      {error && (
        <div className="bg-red-50 text-red-700 rounded-lg p-3 mb-4 text-sm">
          {error}
        </div>
      )}

      {/* Deliverable */}
      <div className="bg-white border border-gray-200 rounded-xl p-6 mb-6">
        <h2 className="font-semibold text-navy-900 mb-3">Deliverable</h2>
        <div className="bg-gray-50 rounded-lg p-4 text-sm">
          {deliverable.content_type === "text/markdown" ? (
            <div className="prose prose-sm max-w-none">
              <ReactMarkdown>{deliverable.content || ""}</ReactMarkdown>
            </div>
          ) : (
            <pre className="whitespace-pre-wrap text-gray-700 font-mono text-xs">
              {deliverable.content || JSON.stringify(deliverable, null, 2)}
            </pre>
          )}
        </div>
      </div>

      {/* Provenance */}
      {provenance && (
        <div className="bg-white border border-gray-200 rounded-xl p-6 mb-6">
          <div className="flex items-center gap-2 mb-3">
            <h2 className="font-semibold text-navy-900">Provenance</h2>
            <ProvenanceBadge tier={bounty.provenance_tier} />
          </div>
          <div className="grid sm:grid-cols-2 gap-3 text-sm">
            {provenance.source_type && (
              <div>
                <span className="text-gray-500">Source Type:</span>{" "}
                <span className="font-medium">{provenance.source_type}</span>
              </div>
            )}
            {provenance.attestation_level && (
              <div>
                <span className="text-gray-500">Attestation:</span>{" "}
                <span className="font-medium">
                  {provenance.attestation_level}
                </span>
              </div>
            )}
            {provenance.content_hash && (
              <div className="sm:col-span-2">
                <span className="text-gray-500">Content Hash:</span>{" "}
                <span className="font-mono text-xs">
                  {provenance.content_hash}
                </span>
              </div>
            )}
            {provenance.source_refs && (
              <div className="sm:col-span-2">
                <span className="text-gray-500">Sources:</span>
                <ul className="list-disc list-inside mt-1 text-xs">
                  {(provenance.source_refs as string[]).map((ref, i) => (
                    <li key={i} className="font-mono">
                      {ref}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Partial approval info banner */}
      {isPartial && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-5 mb-6">
          <div className="flex items-start gap-3">
            <Clock className="w-5 h-5 text-amber-600 mt-0.5 flex-shrink-0" />
            <div className="text-sm">
              <p className="font-semibold text-amber-800 mb-1">
                Awaiting Efficacy Review
              </p>
              <p className="text-amber-700">
                {submission.release_percent}% released (score: {submission.score}
                ). Holdback of {100 - (submission.release_percent || 0)}% is
                pending your review.
              </p>
              {submission.efficacy_check_at && (
                <p className="text-amber-600 mt-1">
                  Review due:{" "}
                  {new Date(submission.efficacy_check_at).toLocaleDateString()}
                </p>
              )}
              {submission.efficacy_criteria && (
                <p className="text-amber-600 mt-1">
                  Criteria: {submission.efficacy_criteria}
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Scored approval UI */}
      {isPending && (
        <div className="bg-white border border-gray-200 rounded-xl p-6 mb-6">
          <h2 className="font-semibold text-navy-900 mb-4 flex items-center gap-2">
            <Star className="w-5 h-5 text-amber-500" /> Score & Release
          </h2>

          {/* Score slider */}
          <div className="mb-5">
            <div className="flex items-center justify-between mb-2">
              <label className="text-sm font-medium text-gray-700">
                Quality Score
              </label>
              <span className={`text-sm font-semibold ${labelColor}`}>
                {score} - {label}
              </span>
            </div>
            <input
              type="range"
              min={0}
              max={100}
              value={score}
              onChange={(e) => {
                const v = Number(e.target.value);
                setScore(v);
                if (!useHoldback) setReleasePercent(v);
              }}
              className="w-full h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-navy-700"
            />
            <div className="flex justify-between text-xs text-gray-400 mt-1">
              <span>0</span>
              <span>25</span>
              <span>50</span>
              <span>75</span>
              <span>100</span>
            </div>
          </div>

          {/* Holdback toggle */}
          <div className="mb-4">
            <label className="flex items-center gap-3 cursor-pointer">
              <input
                type="checkbox"
                checked={useHoldback}
                onChange={(e) => {
                  setUseHoldback(e.target.checked);
                  if (!e.target.checked) setReleasePercent(100);
                  else setReleasePercent(Math.min(score, 99));
                }}
                className="w-4 h-4 rounded border-gray-300 text-navy-700 focus:ring-navy-500"
              />
              <span className="text-sm text-gray-700">
                Hold back a portion for efficacy review
              </span>
            </label>
          </div>

          {/* Holdback config */}
          {useHoldback && (
            <div className="bg-gray-50 rounded-lg p-4 mb-4 space-y-4">
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="text-sm font-medium text-gray-700">
                    Release now
                  </label>
                  <span className="text-sm font-semibold text-navy-900">
                    {releasePercent}%
                    {bounty.reward_amount && (
                      <span className="text-gray-500 font-normal ml-1">
                        ({Math.floor((bounty.reward_amount * releasePercent) / 100)} ATE)
                      </span>
                    )}
                  </span>
                </div>
                <input
                  type="range"
                  min={1}
                  max={99}
                  value={releasePercent}
                  onChange={(e) => setReleasePercent(Number(e.target.value))}
                  className="w-full h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-money"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Holdback: {100 - releasePercent}%
                  {bounty.reward_amount && (
                    <span>
                      {" "}
                      ({bounty.reward_amount - Math.floor((bounty.reward_amount * releasePercent) / 100)} ATE)
                    </span>
                  )}
                </p>
              </div>

              <div>
                <label className="text-sm font-medium text-gray-700 block mb-1">
                  Review in (days)
                </label>
                <input
                  type="number"
                  min={1}
                  max={90}
                  value={checkDays}
                  onChange={(e) => setCheckDays(Number(e.target.value))}
                  className="w-24 px-3 py-2 rounded-lg border border-gray-300 text-sm focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none"
                />
              </div>

              <div>
                <label className="text-sm font-medium text-gray-700 block mb-1">
                  Efficacy criteria (optional)
                </label>
                <input
                  type="text"
                  value={criteria}
                  onChange={(e) => setCriteria(e.target.value)}
                  placeholder="e.g., Prediction accuracy >= 75% after 3 trading days"
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 text-sm focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none"
                />
              </div>
            </div>
          )}

          {/* Notes */}
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={2}
            placeholder="Optional notes or feedback..."
            className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm mb-4"
          />

          {/* Action buttons */}
          <div className="flex flex-wrap gap-3">
            <button
              onClick={handleApprove}
              disabled={acting}
              className="flex items-center gap-2 px-5 py-2.5 bg-money text-navy-900 rounded-lg font-semibold text-sm hover:bg-money-dark transition disabled:opacity-50"
            >
              <CheckCircle className="w-4 h-4" />
              {useHoldback && releasePercent < 100
                ? `Approve ${releasePercent}% & Holdback ${100 - releasePercent}%`
                : "Approve & Release 100%"}
            </button>
            <button
              onClick={handleReject}
              disabled={acting}
              className="flex items-center gap-2 px-5 py-2.5 bg-white border border-gray-300 text-gray-700 rounded-lg font-medium text-sm hover:bg-gray-50 transition disabled:opacity-50"
            >
              <XCircle className="w-4 h-4" /> Reject
            </button>
            <button
              onClick={handleDispute}
              disabled={acting}
              className="flex items-center gap-2 px-5 py-2.5 bg-warning text-white rounded-lg font-medium text-sm hover:bg-warning-dark transition disabled:opacity-50"
            >
              <AlertTriangle className="w-4 h-4" /> Dispute
            </button>
          </div>
        </div>
      )}

      {/* Efficacy review UI */}
      {isPartial && (
        <div className="bg-white border border-gray-200 rounded-xl p-6">
          <h2 className="font-semibold text-navy-900 mb-4 flex items-center gap-2">
            <Star className="w-5 h-5 text-amber-500" /> Efficacy Assessment
          </h2>

          <div className="mb-5">
            <div className="flex items-center justify-between mb-2">
              <label className="text-sm font-medium text-gray-700">
                Efficacy Score
              </label>
              <span
                className={`text-sm font-semibold ${scoreLabel(efficacyScore)[1]}`}
              >
                {efficacyScore} - {scoreLabel(efficacyScore)[0]}
              </span>
            </div>
            <input
              type="range"
              min={0}
              max={100}
              value={efficacyScore}
              onChange={(e) => setEfficacyScore(Number(e.target.value))}
              className="w-full h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-navy-700"
            />
          </div>

          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={2}
            placeholder="Notes on efficacy assessment..."
            className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm mb-4"
          />

          <div className="flex gap-3">
            <button
              onClick={() => handleEfficacyReview("release")}
              disabled={acting}
              className="flex items-center gap-2 px-5 py-2.5 bg-money text-navy-900 rounded-lg font-semibold text-sm hover:bg-money-dark transition disabled:opacity-50"
            >
              <CheckCircle className="w-4 h-4" /> Release Holdback
            </button>
            <button
              onClick={() => handleEfficacyReview("refund")}
              disabled={acting}
              className="flex items-center gap-2 px-5 py-2.5 bg-white border border-red-300 text-red-700 rounded-lg font-medium text-sm hover:bg-red-50 transition disabled:opacity-50"
            >
              <XCircle className="w-4 h-4" /> Refund Holdback
            </button>
          </div>
        </div>
      )}

      {/* Completed state */}
      {!isPending && !isPartial && (
        <div className="bg-gray-50 border border-gray-200 rounded-xl p-6 text-center text-gray-500">
          This submission is{" "}
          <strong>{submission.status.replace("_", " ")}</strong>
          {submission.score != null && (
            <span className="ml-2">(Score: {submission.score})</span>
          )}
          {submission.efficacy_score != null && (
            <span className="ml-2">
              Efficacy: {submission.efficacy_score}
            </span>
          )}
          {submission.reviewer_notes && (
            <p className="mt-2 text-sm">Notes: {submission.reviewer_notes}</p>
          )}
        </div>
      )}
    </div>
  );
}
