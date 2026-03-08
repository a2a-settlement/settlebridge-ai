import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  CheckCircle,
  XCircle,
  AlertTriangle,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import api from "../services/api";
import type { Submission, Bounty } from "../types";
import ProvenanceBadge from "../components/ProvenanceBadge";

export default function ReviewSubmission() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [bounty, setBounty] = useState<Bounty | null>(null);
  const [notes, setNotes] = useState("");
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState("");

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

  const handleAction = async (action: "approve" | "reject" | "dispute") => {
    if (!submission) return;
    setActing(true);
    setError("");
    try {
      const body =
        action === "dispute" ? { reason: notes || "Disputed" } : { notes };
      await api.post(`/submissions/${submission.id}/${action}`, body);
      navigate("/dashboard");
    } catch (err: any) {
      setError(err.response?.data?.detail || `Failed to ${action}`);
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

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <button
        onClick={() => navigate(-1)}
        className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-navy-700 mb-6"
      >
        <ArrowLeft className="w-4 h-4" /> Back
      </button>

      <h1 className="text-2xl font-bold text-navy-900 mb-2">
        Review Submission
      </h1>
      <p className="text-gray-500 text-sm mb-6">
        For bounty: <strong>{bounty.title}</strong>
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
        {deliverable.content_type && (
          <p className="text-xs text-gray-400 mt-2">
            Format: {deliverable.content_type}
          </p>
        )}
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

      {/* Actions */}
      {submission.status === "pending_review" && (
        <div className="bg-white border border-gray-200 rounded-xl p-6">
          <h2 className="font-semibold text-navy-900 mb-3">Your Decision</h2>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            placeholder="Optional notes or feedback..."
            className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm mb-4"
          />
          <div className="flex gap-3">
            <button
              onClick={() => handleAction("approve")}
              disabled={acting}
              className="flex items-center gap-2 px-5 py-2.5 bg-money text-white rounded-lg font-semibold text-sm hover:bg-money-dark transition disabled:opacity-50"
            >
              <CheckCircle className="w-4 h-4" /> Approve & Release Payment
            </button>
            <button
              onClick={() => handleAction("reject")}
              disabled={acting}
              className="flex items-center gap-2 px-5 py-2.5 bg-white border border-gray-300 text-gray-700 rounded-lg font-medium text-sm hover:bg-gray-50 transition disabled:opacity-50"
            >
              <XCircle className="w-4 h-4" /> Reject
            </button>
            <button
              onClick={() => handleAction("dispute")}
              disabled={acting}
              className="flex items-center gap-2 px-5 py-2.5 bg-warning text-white rounded-lg font-medium text-sm hover:bg-warning-dark transition disabled:opacity-50"
            >
              <AlertTriangle className="w-4 h-4" /> Dispute
            </button>
          </div>
        </div>
      )}

      {submission.status !== "pending_review" && (
        <div className="bg-gray-50 border border-gray-200 rounded-xl p-6 text-center text-gray-500">
          This submission is <strong>{submission.status.replace("_", " ")}</strong>
          {submission.reviewer_notes && (
            <p className="mt-2 text-sm">Notes: {submission.reviewer_notes}</p>
          )}
        </div>
      )}
    </div>
  );
}
