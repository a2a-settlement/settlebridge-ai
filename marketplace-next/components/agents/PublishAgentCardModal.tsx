"use client";

import { useEffect, useState } from "react";
import { AlertCircle, FileCode2, FileText, X } from "lucide-react";
import api from "@/services/api";
import {
  extractApiError,
  type PublishAgentCardResponse,
} from "@/lib/managedAgents";

interface Props {
  agentId: string;
  agentName: string;
  hasAgentCard?: boolean;
  initialCard?: Record<string, unknown> | null;
  initialDescription?: string;
  initialSkills?: string[];
  onClose: () => void;
  onPublished: () => void;
}

interface AgentHydrationResponse {
  description?: string;
  skills?: string[];
  agent_card?: Record<string, unknown> | null;
}

function defaultDidForAgent(agentId: string): string {
  return `did:web:exchange.a2a-settlement.org:agents:${agentId}`;
}

function extractSkillStrings(
  card: Record<string, unknown> | null | undefined,
  fallbackSkills?: string[]
): string[] {
  const cap = card?.capabilities as { skills?: unknown } | undefined;
  if (Array.isArray(cap?.skills)) {
    const out = cap.skills
      .map((s) => (typeof s === "string" ? s.trim() : ""))
      .filter(Boolean);
    if (out.length > 0) return out;
  }
  const topSkills = card?.skills;
  if (Array.isArray(topSkills)) {
    const out = topSkills
      .map((s) => {
        if (typeof s === "string") return s.trim();
        if (s && typeof s === "object") {
          const obj = s as { id?: string; name?: string };
          return (obj.id || obj.name || "").trim();
        }
        return "";
      })
      .filter(Boolean);
    if (out.length > 0) return out;
  }
  return (fallbackSkills ?? []).map((s) => s.trim()).filter(Boolean);
}

function buildDefaultRfcCard(params: {
  agentId: string;
  agentName: string;
  description?: string;
  skills?: string[];
  existingCard?: Record<string, unknown> | null;
}): Record<string, unknown> {
  const now = new Date().toISOString();
  const card = params.existingCard;
  const existingMeta =
    card?.metadata && typeof card.metadata === "object"
      ? (card.metadata as Record<string, unknown>)
      : null;
  const existingCap =
    card?.capabilities && typeof card.capabilities === "object"
      ? (card.capabilities as Record<string, unknown>)
      : null;
  const existingSettlement =
    card?.settlement && typeof card.settlement === "object"
      ? (card.settlement as Record<string, unknown>)
      : null;
  const existingIdentity =
    card?.identity && typeof card.identity === "object"
      ? (card.identity as Record<string, unknown>)
      : null;
  const existingPolicies =
    card?.policies && typeof card.policies === "object"
      ? (card.policies as Record<string, unknown>)
      : null;

  const skills = extractSkillStrings(card, params.skills);
  const name =
    typeof card?.name === "string" && card.name.trim()
      ? card.name.trim()
      : params.agentName;
  const id =
    typeof card?.id === "string" && card.id.trim()
      ? card.id.trim()
      : defaultDidForAgent(params.agentId);
  const description =
    typeof card?.description === "string" && card.description.trim()
      ? card.description.trim()
      : params.description?.trim() || "SettleBridge exchange agent";
  const kyaLevel =
    typeof card?.kya_level === "number" ? card.kya_level : 0;

  const metadata: Record<string, unknown> = {
    created:
      typeof existingMeta?.created === "string" && existingMeta.created
        ? existingMeta.created
        : now,
    updated: now,
  };
  if (
    existingMeta?.card_signature &&
    typeof existingMeta.card_signature === "object"
  ) {
    metadata.card_signature = existingMeta.card_signature;
  }

  return {
    protocol_version:
      typeof card?.protocol_version === "string" && card.protocol_version
        ? card.protocol_version
        : "2026.1",
    name,
    id,
    description,
    kya_level: kyaLevel,
    identity:
      existingIdentity && typeof existingIdentity.type === "string"
        ? existingIdentity
        : { type: "sandbox" },
    attestations: Array.isArray(card?.attestations) ? card.attestations : [],
    settlement:
      existingSettlement &&
      Array.isArray(existingSettlement.supported_methods) &&
      typeof existingSettlement.exchange_url === "string"
        ? existingSettlement
        : {
            supported_methods: ["escrow"],
            exchange_url: "https://exchange.a2a-settlement.org",
            token_types: ["ATE"],
          },
    capabilities: {
      skills,
      input_formats: Array.isArray(existingCap?.input_formats)
        ? existingCap.input_formats
        : ["application/json"],
      output_formats: Array.isArray(existingCap?.output_formats)
        ? existingCap.output_formats
        : ["application/json"],
      ...(existingCap?.rate_limit && typeof existingCap.rate_limit === "object"
        ? { rate_limit: existingCap.rate_limit }
        : {}),
    },
    policies: existingPolicies ?? {},
    metadata,
  };
}

export default function PublishAgentCardModal({
  agentId,
  agentName,
  hasAgentCard = false,
  initialCard,
  initialDescription,
  initialSkills,
  onClose,
  onPublished,
}: Props) {
  const needsHydration = initialCard === undefined;
  const [hydrating, setHydrating] = useState(needsHydration);

  const initialPayload = buildDefaultRfcCard({
    agentId,
    agentName,
    description: initialDescription,
    skills: initialSkills,
    existingCard: initialCard ?? null,
  });

  const [baseCard, setBaseCard] = useState<Record<string, unknown>>(initialPayload);
  const [name, setName] = useState(String(initialPayload.name ?? agentName));
  const [didId, setDidId] = useState(
    String(initialPayload.id ?? defaultDidForAgent(agentId))
  );
  const [description, setDescription] = useState(
    String(initialPayload.description ?? "SettleBridge exchange agent")
  );
  const [skillsText, setSkillsText] = useState(
    extractSkillStrings(initialPayload, initialSkills).join(", ")
  );
  const [mode, setMode] = useState<"guided" | "json">("guided");
  const [jsonText, setJsonText] = useState(() =>
    JSON.stringify(initialPayload, null, 2)
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!needsHydration) return;
    let cancelled = false;
    setHydrating(true);
    api
      .get<AgentHydrationResponse>(`/agents/${encodeURIComponent(agentId)}`)
      .then(({ data }) => {
        if (cancelled) return;
        const hydrated = buildDefaultRfcCard({
          agentId,
          agentName,
          description: data.description ?? initialDescription,
          skills: data.skills ?? initialSkills,
          existingCard: data.agent_card ?? null,
        });
        setBaseCard(hydrated);
        setName(String(hydrated.name ?? agentName));
        setDidId(String(hydrated.id ?? defaultDidForAgent(agentId)));
        setDescription(
          String(hydrated.description ?? "SettleBridge exchange agent")
        );
        setSkillsText(
          extractSkillStrings(hydrated, data.skills ?? initialSkills).join(", ")
        );
        setJsonText(JSON.stringify(hydrated, null, 2));
      })
      .catch(() => {
        // Keep KYA L0 default template if public lookup fails.
      })
      .finally(() => {
        if (!cancelled) setHydrating(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentId, agentName, initialDescription, initialSkills, needsHydration]);

  const assembleGuidedPayload = (): Record<string, unknown> => {
    const now = new Date().toISOString();
    const skills = skillsText
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const prevCap =
      baseCard.capabilities && typeof baseCard.capabilities === "object"
        ? (baseCard.capabilities as Record<string, unknown>)
        : {};
    const prevMeta =
      baseCard.metadata && typeof baseCard.metadata === "object"
        ? (baseCard.metadata as Record<string, unknown>)
        : {};

    return {
      ...baseCard,
      name: name.trim() || agentName,
      id: didId.trim() || defaultDidForAgent(agentId),
      description: description.trim() || "SettleBridge exchange agent",
      capabilities: {
        input_formats: ["application/json"],
        output_formats: ["application/json"],
        ...prevCap,
        skills,
      },
      metadata: {
        created:
          typeof prevMeta.created === "string" && prevMeta.created
            ? prevMeta.created
            : now,
        ...prevMeta,
        updated: now,
      },
    };
  };

  const switchToJson = () => {
    setError("");
    const current = assembleGuidedPayload();
    setJsonText(JSON.stringify(current, null, 2));
    setMode("json");
  };

  const switchToGuided = () => {
    try {
      const parsed = JSON.parse(jsonText) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        setError("Agent Card JSON must be an object.");
        return;
      }
      const obj = parsed as Record<string, unknown>;
      setBaseCard(obj);
      if (typeof obj.name === "string") setName(obj.name);
      if (typeof obj.id === "string") setDidId(obj.id);
      if (typeof obj.description === "string") setDescription(obj.description);
      setSkillsText(extractSkillStrings(obj).join(", "));
      setError("");
      setMode("guided");
    } catch {
      setError("Fix JSON syntax errors before switching back to Guided mode.");
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    let payload: Record<string, unknown>;
    if (mode === "json") {
      try {
        const parsed = JSON.parse(jsonText) as unknown;
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          setError("Agent Card JSON must be a valid JSON object.");
          return;
        }
        payload = parsed as Record<string, unknown>;
      } catch {
        setError("Invalid JSON syntax. Check commas, quotes, and braces.");
        return;
      }
    } else {
      if (!name.trim()) {
        setError("Name is required.");
        return;
      }
      if (!didId.trim()) {
        setError("DID / Agent ID is required.");
        return;
      }
      payload = assembleGuidedPayload();
    }

    setSubmitting(true);
    try {
      await api.put<PublishAgentCardResponse>(
        `/bots/${encodeURIComponent(agentId)}/card`,
        payload
      );
      onPublished();
    } catch (err: unknown) {
      setError(extractApiError(err, "Failed to publish Agent Card"));
    } finally {
      setSubmitting(false);
    }
  };

  const isHigherKya =
    typeof baseCard.kya_level === "number" && baseCard.kya_level >= 1;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40">
      <div
        className="bg-white border border-gray-200 rounded-xl max-w-2xl w-full p-6 shadow-xl max-h-[92vh] overflow-y-auto"
        role="dialog"
        aria-modal="true"
        aria-labelledby="publish-card-title"
      >
        <div className="flex items-center justify-between gap-3 mb-4">
          <div>
            <h3
              id="publish-card-title"
              className="text-navy-900 font-semibold text-base"
            >
              {hasAgentCard ? "Edit Agent Card" : "Publish Agent Card"}
            </h3>
            <p className="text-xs text-gray-500 mt-0.5">
              RFC-001 Agent Card for <span className="font-medium text-navy-800">{agentName}</span>
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            aria-label="Close modal"
            className="p-1 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-navy-500"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div
          className="inline-flex gap-1 p-1 bg-gray-100 rounded-lg mb-4"
          role="tablist"
          aria-label="Agent Card editor mode"
        >
          <button
            type="button"
            role="tab"
            aria-selected={mode === "guided"}
            onClick={mode === "json" ? switchToGuided : undefined}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition focus:outline-none focus:ring-2 focus:ring-navy-500 ${
              mode === "guided"
                ? "bg-white text-navy-900 shadow-sm"
                : "text-gray-600 hover:text-navy-900"
            }`}
          >
            <FileText className="w-3.5 h-3.5" />
            Guided
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "json"}
            onClick={mode === "guided" ? switchToJson : undefined}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition focus:outline-none focus:ring-2 focus:ring-navy-500 ${
              mode === "json"
                ? "bg-white text-navy-900 shadow-sm"
                : "text-gray-600 hover:text-navy-900"
            }`}
          >
            <FileCode2 className="w-3.5 h-3.5" />
            Advanced JSON
          </button>
        </div>

        {isHigherKya && mode === "guided" && (
          <div className="bg-blue-50 border border-blue-200 text-blue-800 rounded-lg p-3 mb-4 text-xs">
            This card uses KYA Level {String(baseCard.kya_level)} verification. If your card carries a cryptographic signature, use <strong>Advanced JSON</strong> to paste a freshly signed payload.
          </div>
        )}

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg p-3 mb-4 flex items-start gap-2 text-sm">
            <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {hydrating ? (
          <div className="space-y-3 py-6">
            <div className="h-10 bg-gray-100 rounded-lg animate-pulse" />
            <div className="h-10 bg-gray-100 rounded-lg animate-pulse" />
            <div className="h-20 bg-gray-100 rounded-lg animate-pulse" />
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            {mode === "guided" ? (
              <>
                <div>
                  <label
                    htmlFor="card-agent-name"
                    className="block text-sm font-medium text-gray-700 mb-1"
                  >
                    Name
                  </label>
                  <input
                    id="card-agent-name"
                    type="text"
                    required
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    className="w-full px-3 py-2.5 rounded-lg border border-gray-300 text-sm focus:outline-none focus:ring-2 focus:ring-navy-500"
                  />
                </div>

                <div>
                  <label
                    htmlFor="card-did-id"
                    className="block text-sm font-medium text-gray-700 mb-1"
                  >
                    DID / ID
                  </label>
                  <input
                    id="card-did-id"
                    type="text"
                    required
                    value={didId}
                    onChange={(e) => setDidId(e.target.value)}
                    className="w-full px-3 py-2.5 rounded-lg border border-gray-300 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-navy-500"
                  />
                </div>

                <div>
                  <label
                    htmlFor="card-description"
                    className="block text-sm font-medium text-gray-700 mb-1"
                  >
                    Description
                  </label>
                  <textarea
                    id="card-description"
                    rows={3}
                    required
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="Describe this agent's capabilities and settlement behavior…"
                    className="w-full px-3 py-2 rounded-lg border border-gray-300 text-sm focus:outline-none focus:ring-2 focus:ring-navy-500"
                  />
                </div>

                <div>
                  <label
                    htmlFor="card-skills"
                    className="block text-sm font-medium text-gray-700 mb-1"
                  >
                    Skills <span className="text-gray-400 font-normal">(comma-separated)</span>
                  </label>
                  <input
                    id="card-skills"
                    type="text"
                    value={skillsText}
                    onChange={(e) => setSkillsText(e.target.value)}
                    placeholder="security-audit, code-review, python"
                    className="w-full px-3 py-2.5 rounded-lg border border-gray-300 text-sm focus:outline-none focus:ring-2 focus:ring-navy-500"
                  />
                </div>

                <p className="text-xs text-gray-500">
                  Defaults to KYA Level 0 (<code className="bg-gray-100 px-1 rounded">sandbox</code>) with ATE escrow settlement. Switch to <strong>Advanced JSON</strong> for KYA Level 1/2 signed cards.
                </p>
              </>
            ) : (
              <div>
                <label
                  htmlFor="card-raw-json"
                  className="block text-sm font-medium text-gray-700 mb-1"
                >
                  RFC-001 Agent Card JSON
                </label>
                <textarea
                  id="card-raw-json"
                  rows={16}
                  spellCheck={false}
                  value={jsonText}
                  onChange={(e) => setJsonText(e.target.value)}
                  className="w-full px-3 py-2.5 rounded-lg border border-gray-300 text-xs font-mono leading-relaxed focus:outline-none focus:ring-2 focus:ring-navy-500"
                />
              </div>
            )}

            <div className="flex justify-end gap-3 pt-2">
              <button
                type="button"
                onClick={onClose}
                disabled={submitting}
                className="px-4 py-2 text-sm font-medium text-gray-600 hover:text-gray-900 rounded-lg focus:outline-none focus:ring-2 focus:ring-navy-500"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting}
                className="px-4 py-2 bg-navy-900 text-white rounded-lg text-sm font-semibold hover:bg-navy-800 transition disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-navy-500"
              >
                {submitting
                  ? "Publishing…"
                  : hasAgentCard
                    ? "Save Agent Card"
                    : "Publish Agent Card"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
