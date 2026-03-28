import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, Bot, ShieldCheck } from "lucide-react";
import api from "../services/api";
import ReputationScore from "../components/ReputationScore";
import AttestationFreshnessBadge from "../components/AttestationFreshnessBadge";
import type { AttestationFreshness } from "../types";

interface GatewayClaimInfo {
  gateway_id: string;
  gateway_name: string;
  verified: boolean;
  claimed_at: string;
}

interface AgentDetail {
  id: string;
  bot_name: string;
  developer_id?: string;
  developer_name?: string;
  description?: string;
  skills?: string[];
  reputation?: number;
  status?: string;
  created_at?: string;
  attestation_freshness?: AttestationFreshness | null;
  gateway_claims?: GatewayClaimInfo[] | null;
}

export default function AgentProfile() {
  const { botId } = useParams<{ botId: string }>();
  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<AgentDetail>(`/agents/${botId}`)
      .then(({ data }) => setAgent(data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [botId]);

  if (loading) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-8 animate-pulse">
        <div className="h-8 bg-gray-200 rounded w-1/3 mb-4" />
        <div className="h-48 bg-gray-200 rounded" />
      </div>
    );
  }

  if (!agent) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-20 text-center text-gray-500">
        Agent not found
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <Link
        to="/agents"
        className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-navy-700 mb-6"
      >
        <ArrowLeft className="w-4 h-4" /> Back to directory
      </Link>

      <div className="bg-white border border-gray-200 rounded-2xl p-6 sm:p-8">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:gap-4 mb-6">
          <div className="w-16 h-16 bg-navy-100 rounded-2xl flex items-center justify-center flex-shrink-0">
            <Bot className="w-8 h-8 text-navy-700" />
          </div>
          <div className="flex-1 min-w-0">
            <h1 className="text-2xl font-bold text-navy-900">
              {agent.bot_name}
            </h1>
            {agent.developer_id && (
              <p className="text-gray-500 text-sm break-all">{agent.developer_id}</p>
            )}
          </div>
          <div className="text-left sm:text-right flex-shrink-0">
            <ReputationScore
              score={agent.reputation ?? null}
              size="lg"
            />
            <div className="mt-1">
              <AttestationFreshnessBadge
                freshness={agent.attestation_freshness ?? null}
              />
            </div>
          </div>
        </div>

        {agent.description && (
          <p className="text-gray-600 mb-6">{agent.description}</p>
        )}

        <div className="grid sm:grid-cols-2 gap-6 mb-6">
          <div className="bg-gray-50 rounded-xl p-4">
            <p className="text-sm text-gray-500">Status</p>
            <p className="text-2xl font-bold text-navy-900 capitalize">
              {agent.status ?? "unknown"}
            </p>
          </div>
          <div className="bg-gray-50 rounded-xl p-4">
            <p className="text-sm text-gray-500">Member Since</p>
            <p className="text-2xl font-bold text-navy-900">
              {agent.created_at
                ? new Date(agent.created_at).toLocaleDateString()
                : "—"}
            </p>
          </div>
        </div>

        {agent.gateway_claims && agent.gateway_claims.length > 0 && (
          <div className="mb-6">
            <h3 className="font-semibold text-navy-900 text-sm mb-3">Managed By</h3>
            <div className="flex flex-wrap gap-2">
              {agent.gateway_claims.map((claim) => (
                <span
                  key={claim.gateway_id}
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium ${
                    claim.verified
                      ? "bg-green-50 text-green-700 border border-green-200"
                      : "bg-blue-50 text-blue-700 border border-blue-200"
                  }`}
                >
                  <ShieldCheck className="w-4 h-4" />
                  {claim.gateway_name || "Gateway"}
                  {claim.verified && (
                    <span className="text-xs font-normal opacity-70">Verified</span>
                  )}
                </span>
              ))}
            </div>
          </div>
        )}

        {agent.skills && agent.skills.length > 0 && (
          <div>
            <h3 className="font-semibold text-navy-900 text-sm mb-3">Skills</h3>
            <div className="flex flex-wrap gap-2">
              {agent.skills.map((skill) => (
                <span
                  key={skill}
                  className="px-3 py-1.5 bg-navy-100 text-navy-700 rounded-full text-sm font-medium"
                >
                  {skill}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
