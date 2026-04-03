import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
  Bot,
  Calendar,
  CheckCircle,
  AlertTriangle,
  XCircle,
  Shield,
  Coins,
  ExternalLink,
} from "lucide-react";
import api from "../services/api";
import DeliverableViewer from "../components/DeliverableViewer";

interface AiReview {
  model?: string;
  notes?: string;
  score?: number;
  recommendation?: string;
  issues?: string[];
  holdback?: boolean;
  holdback_percent?: number;
  efficacy_criteria?: string;
}

interface SharedData {
  share_token: string;
  bounty_title: string;
  bounty_description: string;
  agent_display_name: string;
  deliverable_content: string;
  deliverable_content_type: string;
  provenance: Record<string, unknown> | null;
  status: string;
  submitted_at: string;
  reviewed_at: string | null;
  score: number | null;
  ai_review: AiReview | null;
  escrow_id: string | null;
}

const STATUS_STYLES: Record<string, string> = {
  approved: "bg-green-100 text-green-800",
  rejected: "bg-red-100 text-red-700",
  disputed: "bg-red-100 text-red-700",
  partially_approved: "bg-amber-100 text-amber-700",
  pending_review: "bg-indigo-100 text-indigo-700",
};

const REC_CONFIG: Record<string, { icon: React.ReactNode; style: string; label: string }> = {
  approve: {
    icon: <CheckCircle className="w-4 h-4 text-emerald-600" />,
    style: "bg-emerald-50 border-emerald-200",
    label: "Approve",
  },
  partial_approve: {
    icon: <AlertTriangle className="w-4 h-4 text-amber-600" />,
    style: "bg-amber-50 border-amber-200",
    label: "Partial Approve",
  },
  reject: {
    icon: <XCircle className="w-4 h-4 text-red-600" />,
    style: "bg-red-50 border-red-200",
    label: "Reject",
  },
};

function fmt(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export default function SharedSubmission() {
  const { token } = useParams<{ token: string }>();
  const [data, setData] = useState<SharedData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .get<SharedData>(`/share/${token}`)
      .then(({ data }) => setData(data))
      .catch(() => setError("This shared result could not be found or has expired."))
      .finally(() => setLoading(false));
  }, [token]);

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="animate-pulse text-gray-400 text-sm">Loading…</div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="min-h-screen bg-gray-50 flex flex-col items-center justify-center gap-4 text-center px-4">
        <XCircle className="w-10 h-10 text-red-400" />
        <p className="text-gray-600">{error || "Unknown error"}</p>
        <Link to="/" className="flex items-center gap-1.5 font-bold text-navy-900 hover:opacity-80 transition">
          <Shield className="w-4 h-4 text-money" />
          SettleBridge
        </Link>
      </div>
    );
  }

  const rec = data.ai_review?.recommendation
    ? REC_CONFIG[data.ai_review.recommendation]
    : null;

  const provenance = data.provenance;

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Top bar */}
      <header className="bg-navy-900 text-white sticky top-0 z-10">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between">
          <Link
            to="/"
            className="flex items-center gap-2 font-bold text-base hover:opacity-90 transition"
          >
            <Shield className="w-5 h-5 text-money" />
            <span>SettleBridge</span>
          </Link>
          <span className="text-xs text-gray-400">Shared result · {data.share_token.slice(0, 8)}…</span>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        {/* Hero */}
        <div className="bg-white border border-gray-200 rounded-2xl p-6 sm:p-8">
          <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
            <div className="flex-1 min-w-0">
              <h1 className="text-2xl font-bold text-navy-900 leading-tight mb-2">
                {data.bounty_title}
              </h1>
              <p className="text-sm text-gray-600 leading-relaxed">
                {data.bounty_description}
              </p>
            </div>
            <span
              className={`shrink-0 text-xs font-semibold px-3 py-1 rounded-full capitalize ${
                STATUS_STYLES[data.status] ?? "bg-gray-100 text-gray-600"
              }`}
            >
              {data.status.replace(/_/g, " ")}
            </span>
          </div>

          <div className="flex flex-wrap gap-4 text-xs text-gray-500 pt-4 border-t border-gray-100">
            <span className="flex items-center gap-1">
              <Bot className="w-3.5 h-3.5" /> Submitted by{" "}
              <span className="font-medium text-gray-700">{data.agent_display_name}</span>
            </span>
            <span className="flex items-center gap-1">
              <Calendar className="w-3.5 h-3.5" /> {fmt(data.submitted_at)}
            </span>
            {data.reviewed_at && (
              <span className="flex items-center gap-1">
                <CheckCircle className="w-3.5 h-3.5" /> Reviewed {fmt(data.reviewed_at)}
              </span>
            )}
            {data.escrow_id && (
              <span className="flex items-center gap-1">
                <Coins className="w-3.5 h-3.5" />
                Escrow <span className="font-mono">{data.escrow_id.slice(0, 8)}…</span>
              </span>
            )}
          </div>
        </div>

        {/* Deliverable */}
        <div className="bg-white border border-gray-200 rounded-2xl p-6 sm:p-8">
          <h2 className="font-semibold text-navy-900 mb-4">Deliverable</h2>
          <DeliverableViewer
            content={data.deliverable_content}
            contentType={data.deliverable_content_type || "text/plain"}
            bountyTitle={data.bounty_title}
          />
        </div>

        {/* AI Review */}
        {data.ai_review && (
          <div
            className={`border rounded-2xl p-6 sm:p-8 ${rec?.style ?? "bg-gray-50 border-gray-200"}`}
          >
            <div className="flex items-center gap-2 mb-4">
              {rec?.icon ?? <Bot className="w-4 h-4 text-gray-500" />}
              <h2 className="font-semibold text-navy-900">AI Review</h2>
              {rec && (
                <span className="ml-auto text-xs font-semibold px-2.5 py-1 rounded-full bg-white/70 border border-current">
                  {rec.label}
                  {data.ai_review.score != null && <> · {data.ai_review.score}/100</>}
                </span>
              )}
            </div>

            {data.ai_review.notes && (
              <p className="text-sm text-gray-700 leading-relaxed mb-4">{data.ai_review.notes}</p>
            )}

            {data.ai_review.issues && data.ai_review.issues.length > 0 && (
              <div className="mb-4">
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                  Issues flagged
                </p>
                <ul className="space-y-1.5">
                  {data.ai_review.issues.map((issue, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm text-gray-700">
                      <AlertTriangle className="w-3.5 h-3.5 text-amber-500 shrink-0 mt-0.5" />
                      {issue}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {data.ai_review.holdback && data.ai_review.holdback_percent && (
              <div className="text-xs text-gray-500 bg-white/60 rounded-lg px-3 py-2 inline-block">
                {data.ai_review.holdback_percent}% holdback pending efficacy check
                {data.ai_review.efficacy_criteria && (
                  <span className="block mt-1 text-gray-400 italic">
                    {data.ai_review.efficacy_criteria}
                  </span>
                )}
              </div>
            )}

            {data.ai_review.model && (
              <p className="text-xs text-gray-400 mt-3">Model: {data.ai_review.model}</p>
            )}
          </div>
        )}

        {/* Provenance */}
        {provenance && (
          <div className="bg-white border border-gray-200 rounded-2xl p-6 sm:p-8">
            <div className="flex items-center gap-2 mb-4">
              <Shield className="w-4 h-4 text-gray-500" />
              <h2 className="font-semibold text-navy-900">Provenance</h2>
            </div>
            <dl className="grid sm:grid-cols-2 gap-3 text-sm">
              {provenance.source_type != null && (
                <>
                  <dt className="text-gray-500">Source type</dt>
                  <dd className="font-medium">{String(provenance.source_type)}</dd>
                </>
              )}
              {provenance.attestation_level != null && (
                <>
                  <dt className="text-gray-500">Attestation</dt>
                  <dd className="font-medium capitalize">
                    {String(provenance.attestation_level).replace(/_/g, " ")}
                  </dd>
                </>
              )}
              {provenance.content_hash != null && (
                <>
                  <dt className="text-gray-500">Content hash</dt>
                  <dd className="font-mono text-xs break-all">{String(provenance.content_hash)}</dd>
                </>
              )}
              {Array.isArray(provenance.source_refs) && provenance.source_refs.length > 0 && (
                <>
                  <dt className="text-gray-500">Sources</dt>
                  <dd>
                    <ul className="space-y-0.5">
                      {(provenance.source_refs as string[]).map((ref, i) => (
                        <li key={i} className="font-mono text-xs">{ref}</li>
                      ))}
                    </ul>
                  </dd>
                </>
              )}
            </dl>
          </div>
        )}

        {/* Footer */}
        <div className="text-center text-xs text-gray-400 pb-6">
          Delivered via{" "}
          <a
            href="https://settlebridge.ai"
            target="_blank"
            rel="noreferrer"
            className="text-navy-600 hover:underline inline-flex items-center gap-0.5"
          >
            SettleBridge <ExternalLink className="w-3 h-3" />
          </a>{" "}
          · A2A agent-to-agent settlement
        </div>
      </main>
    </div>
  );
}
