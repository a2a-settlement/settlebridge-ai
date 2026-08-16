"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Bot, ExternalLink, ShieldCheck } from "lucide-react";
import api from "@/services/api";
import ReputationScore from "@/components/ReputationScore";
import AttestationFreshnessBadge from "@/components/AttestationFreshnessBadge";
import type { AttestationFreshness } from "@/lib/types";

interface GatewayClaimInfo {
  gateway_id: string;
  gateway_name: string;
  verified: boolean;
  claimed_at: string;
}

interface AgentCardSkill {
  id?: string;
  name?: string;
  description?: string;
  inputModes?: string[];
  outputModes?: string[];
  outputSchema?: string;
}

interface AgentCard {
  name?: string;
  description?: string;
  url?: string;
  version?: string;
  authentication?: { type?: string; description?: string };
  skills?: AgentCardSkill[] | string[];
  metadata?: { well_known_url?: string };
  [key: string]: unknown;
}

interface SkillEvidence {
  skill_id: string;
  settled_count: number;
  avg_score: number | null;
  evidenced: boolean;
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
  agent_card?: AgentCard | null;
  has_agent_card?: boolean;
  kya_level_verified?: number | null;
  skill_evidence?: SkillEvidence[];
}

function isRichSkill(s: unknown): s is AgentCardSkill {
  return typeof s === "object" && s !== null && ("id" in s || "name" in s);
}

export default function AgentProfile() {
  const params = useParams();
  const botId = params.botId as string;
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

  const card = agent.agent_card;
  const richSkills = (card?.skills || []).filter(isRichSkill);
  const tagSkills =
    richSkills.length > 0
      ? []
      : agent.skills || [];
  const evidenceBySkill = Object.fromEntries(
    (agent.skill_evidence || []).map((e) => [e.skill_id, e])
  );

  function SkillTrackRecord({ skillId }: { skillId?: string }) {
    if (!skillId) {
      return (
        <p className="text-xs text-gray-400 mt-2">no settled history</p>
      );
    }
    const ev = evidenceBySkill[skillId];
    if (!ev || ev.settled_count === 0) {
      return (
        <p className="text-xs text-gray-400 mt-2">no settled history</p>
      );
    }
    return (
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        <span className="text-gray-600">
          {ev.settled_count} settled
          {ev.avg_score != null ? ` · avg ${ev.avg_score}` : ""}
        </span>
        {ev.evidenced && (
          <span className="px-2 py-0.5 rounded-full bg-green-50 text-green-700 border border-green-200 font-medium">
            Evidenced
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <Link
        href="/agents"
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
            <div className="mt-2 flex flex-wrap gap-2">
              {agent.has_agent_card ? (
                <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-green-50 text-green-700 border border-green-200">
                  Agent Card published
                  {agent.kya_level_verified != null &&
                    ` · KYA L${agent.kya_level_verified}`}
                </span>
              ) : (
                <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-amber-50 text-amber-800 border border-amber-200">
                  No Agent Card — endpoints undiscoverable
                </span>
              )}
            </div>
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

        {(card?.description || agent.description) && (
          <p className="text-gray-600 mb-6">
            {card?.description || agent.description}
          </p>
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

        {card && (card.url || card.authentication || card.metadata?.well_known_url) && (
          <div className="mb-6 rounded-xl border border-gray-200 bg-gray-50/80 p-4 space-y-3">
            <h3 className="font-semibold text-navy-900 text-sm">How to call</h3>
            {card.url && (
              <div>
                <p className="text-xs text-gray-500 mb-0.5">
                  A2A endpoint{" "}
                  <span className="text-gray-400">(POST only — not openable in a browser)</span>
                </p>
                <code className="block text-sm font-mono text-navy-800 break-all bg-white border border-gray-200 rounded-lg px-3 py-2 select-all">
                  {card.url}
                </code>
              </div>
            )}
            {card.authentication && (
              <div>
                <p className="text-xs text-gray-500 mb-0.5">Authentication</p>
                <p className="text-sm text-gray-800">
                  {card.authentication.type || "bearer"}
                  {card.authentication.description
                    ? ` — ${card.authentication.description}`
                    : ""}
                </p>
              </div>
            )}
            {card.metadata?.well_known_url && (
              <div>
                <p className="text-xs text-gray-500 mb-0.5">Agent Card (browsable)</p>
                <a
                  href={card.metadata.well_known_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 text-sm font-mono text-navy-700 hover:underline break-all"
                >
                  {card.metadata.well_known_url}
                  <ExternalLink className="w-3.5 h-3.5 flex-shrink-0" />
                </a>
              </div>
            )}
            {card.version && (
              <p className="text-xs text-gray-500">Card version {card.version}</p>
            )}
          </div>
        )}

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

        {richSkills.length > 0 ? (
          <div>
            <h3 className="font-semibold text-navy-900 text-sm mb-3">Skills</h3>
            <div className="space-y-3">
              {richSkills.map((skill) => (
                <div
                  key={skill.id || skill.name}
                  className="rounded-xl border border-gray-200 p-4"
                >
                  <div className="flex flex-wrap items-baseline gap-2 mb-1">
                    <span className="font-semibold text-navy-900 text-sm">
                      {skill.name || skill.id}
                    </span>
                    {skill.id && skill.name && (
                      <span className="text-xs font-mono text-gray-400">{skill.id}</span>
                    )}
                  </div>
                  {skill.description && (
                    <p className="text-sm text-gray-600 mb-2">{skill.description}</p>
                  )}
                  <div className="flex flex-wrap gap-3 text-xs text-gray-500">
                    {skill.inputModes && skill.inputModes.length > 0 && (
                      <span>in: {skill.inputModes.join(", ")}</span>
                    )}
                    {skill.outputModes && skill.outputModes.length > 0 && (
                      <span>out: {skill.outputModes.join(", ")}</span>
                    )}
                    {skill.outputSchema && (
                      <a
                        href={skill.outputSchema}
                        target="_blank"
                        rel="noreferrer"
                        className="text-navy-600 hover:underline inline-flex items-center gap-1"
                      >
                        schema <ExternalLink className="w-3 h-3" />
                      </a>
                    )}
                  </div>
                  <SkillTrackRecord skillId={skill.id} />
                </div>
              ))}
            </div>
          </div>
        ) : tagSkills.length > 0 ? (
          <div>
            <h3 className="font-semibold text-navy-900 text-sm mb-3">Skills</h3>
            <div className="space-y-2">
              {tagSkills.map((skill) => (
                <div
                  key={skill}
                  className="flex flex-wrap items-center gap-2"
                >
                  <span className="px-3 py-1.5 bg-navy-100 text-navy-700 rounded-full text-sm font-medium">
                    {skill}
                  </span>
                  <SkillTrackRecord skillId={skill} />
                </div>
              ))}
            </div>
            <p className="text-xs text-amber-700 mt-3">
              Skill tags only — publish an Agent Card for endpoints and schemas.
            </p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
