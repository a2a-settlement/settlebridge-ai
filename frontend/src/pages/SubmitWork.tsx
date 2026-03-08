import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, AlertCircle } from "lucide-react";
import api from "../services/api";
import type { Bounty, Claim } from "../types";

export default function SubmitWork() {
  const { id: claimId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [claim, setClaim] = useState<Claim | null>(null);
  const [bounty, setBounty] = useState<Bounty | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const [content, setContent] = useState("");
  const [contentType, setContentType] = useState("text/plain");
  const [sourceType, setSourceType] = useState("generated");
  const [sourceRefs, setSourceRefs] = useState("");
  const [contentHash, setContentHash] = useState("");
  const [attestationLevel, setAttestationLevel] = useState("self_declared");

  useEffect(() => {
    // We need to fetch claim details — since we don't have a direct claim GET endpoint,
    // we'll use the claims list endpoint
    api
      .get<Claim[]>("/bounties/my/claimed")
      .then(async ({ data }) => {
        const c = data.find((cl) => cl.id === claimId);
        if (c) {
          setClaim(c);
          const b = await api.get<Bounty>(`/bounties/${c.bounty_id}`);
          setBounty(b.data);

          // Pre-fill attestation based on bounty's provenance tier
          const tier = b.data.provenance_tier;
          if (tier === "tier2_signed") setAttestationLevel("signed");
          else if (tier === "tier3_verifiable")
            setAttestationLevel("verifiable");
        }
      })
      .finally(() => setLoading(false));
  }, [claimId]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!claim) return;
    setError("");
    setSubmitting(true);
    try {
      const body = {
        deliverable: {
          content,
          content_type: contentType,
        },
        provenance: {
          source_type: sourceType,
          source_refs: sourceRefs
            ? sourceRefs.split("\n").map((r) => r.trim()).filter(Boolean)
            : undefined,
          content_hash: contentHash || undefined,
          attestation_level: attestationLevel,
        },
      };
      await api.post(`/claims/${claim.id}/submit`, body);
      navigate("/dashboard");
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      if (typeof detail === "object" && detail?.provenance_errors) {
        setError(
          "Provenance errors: " + detail.provenance_errors.join("; ")
        );
      } else {
        setError(typeof detail === "string" ? detail : "Submission failed");
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-8 animate-pulse">
        <div className="h-8 bg-gray-200 rounded w-1/2 mb-4" />
        <div className="h-64 bg-gray-200 rounded" />
      </div>
    );
  }

  if (!claim || !bounty) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-20 text-center text-gray-500">
        Claim not found
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <button
        onClick={() => navigate(-1)}
        className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-navy-700 mb-6"
      >
        <ArrowLeft className="w-4 h-4" /> Back
      </button>

      <h1 className="text-2xl font-bold text-navy-900 mb-2">Submit Work</h1>
      <p className="text-gray-500 text-sm mb-8">
        For bounty: <strong>{bounty.title}</strong>
      </p>

      {error && (
        <div className="bg-red-50 text-red-700 rounded-lg p-3 mb-6 flex items-start gap-2 text-sm">
          <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" /> {error}
        </div>
      )}

      {bounty.acceptance_criteria && (
        <div className="bg-blue-50 border border-blue-200 rounded-xl p-5 mb-6">
          <h3 className="font-semibold text-navy-900 text-sm mb-2">
            Acceptance Criteria
          </h3>
          <p className="text-sm text-gray-700">
            {(bounty.acceptance_criteria as any).description || "See bounty details"}
          </p>
          {(bounty.acceptance_criteria as any).output_format && (
            <p className="text-xs text-gray-500 mt-1">
              Expected format: {(bounty.acceptance_criteria as any).output_format}
            </p>
          )}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-6">
        <div className="bg-white border border-gray-200 rounded-xl p-6 space-y-5">
          <h2 className="font-semibold text-navy-900">Deliverable</h2>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Content
            </label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              required
              rows={12}
              className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm font-mono"
              placeholder="Paste your deliverable content here..."
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Content Type
            </label>
            <select
              value={contentType}
              onChange={(e) => setContentType(e.target.value)}
              className="w-full px-4 py-2.5 rounded-lg border border-gray-300 text-sm focus:ring-2 focus:ring-navy-500 outline-none"
            >
              <option value="text/plain">Plain Text</option>
              <option value="text/markdown">Markdown</option>
              <option value="application/json">JSON</option>
              <option value="text/csv">CSV</option>
              <option value="text/x-python">Python Code</option>
            </select>
          </div>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 space-y-5">
          <h2 className="font-semibold text-navy-900">Provenance</h2>
          <p className="text-sm text-gray-500">
            Required tier:{" "}
            <strong>
              {bounty.provenance_tier.replace(/_/g, " ")}
            </strong>
          </p>

          <div className="grid sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Source Type
              </label>
              <select
                value={sourceType}
                onChange={(e) => setSourceType(e.target.value)}
                className="w-full px-4 py-2.5 rounded-lg border border-gray-300 text-sm focus:ring-2 focus:ring-navy-500 outline-none"
              >
                <option value="generated">Generated</option>
                <option value="api">API</option>
                <option value="database">Database</option>
                <option value="web">Web</option>
                <option value="hybrid">Hybrid</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Attestation Level
              </label>
              <select
                value={attestationLevel}
                onChange={(e) => setAttestationLevel(e.target.value)}
                className="w-full px-4 py-2.5 rounded-lg border border-gray-300 text-sm focus:ring-2 focus:ring-navy-500 outline-none"
              >
                <option value="self_declared">Self Declared</option>
                <option value="signed">Signed</option>
                <option value="verifiable">Verifiable</option>
              </select>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Source References (one per line)
            </label>
            <textarea
              value={sourceRefs}
              onChange={(e) => setSourceRefs(e.target.value)}
              rows={3}
              className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm font-mono"
              placeholder="https://api.example.com/data&#10;https://source.example.com/doc"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Content Hash (SHA-256)
            </label>
            <input
              type="text"
              value={contentHash}
              onChange={(e) => setContentHash(e.target.value)}
              className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm font-mono"
              placeholder="Optional — hash of your raw source data"
            />
          </div>
        </div>

        <div className="flex justify-end gap-3">
          <button
            type="button"
            onClick={() => navigate(-1)}
            className="px-6 py-2.5 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="px-6 py-2.5 bg-navy-900 text-white rounded-lg font-semibold text-sm hover:bg-navy-800 transition disabled:opacity-50"
          >
            {submitting ? "Submitting..." : "Submit Work"}
          </button>
        </div>
      </form>
    </div>
  );
}
