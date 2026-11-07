"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import axios from "axios";
import { AlertCircle, Check, CheckCircle, Copy, Key, Loader2, Wrench } from "lucide-react";
import api from "@/services/api";

interface NextStep {
  code: string;
  message: string;
  blocking: boolean;
  url: string | null;
}

interface EligibleBounty {
  bounty_id: string;
  repo: string;
  issue_number: number;
  title: string;
  amount_ate: number;
  required_ema: number;
}

interface SolverSetupStatus {
  exchange_account_id: string | null;
  github_login: string | null;
  reputation: number | null;
  financial_scope: "granted" | "denied" | "unknown";
  eligible_bounties: EligibleBounty[];
  ineligible_count: number;
  next_steps: NextStep[];
  ready: boolean;
  mcp_config: {
    mcpServers: {
      "a2a-settlement": {
        command: string;
        env: Record<string, string>;
      };
    };
  };
  urls: {
    link_github: string;
    rotate_key: string;
    bounties: string;
  };
}

function messageFrom(err: unknown, fallback: string): string {
  if (!axios.isAxiosError(err)) return fallback;
  const detail = err.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  return fallback;
}

function reputationText(value: number | null): string {
  return value === null ? "not available" : value.toFixed(2);
}

function githubText(login: string | null): string {
  return login && login.trim() ? login : "not linked";
}

function financialScopeLabel(scope: SolverSetupStatus["financial_scope"]): string {
  if (scope === "granted") return "Granted";
  if (scope === "denied") return "Not granted";
  return "Not checked";
}

function isExternal(href: string): boolean {
  return /^https?:\/\//i.test(href);
}

function TextLink({
  href,
  className,
  children,
}: {
  href: string;
  className?: string;
  children: React.ReactNode;
}) {
  if (isExternal(href)) {
    return (
      <a href={href} className={className}>
        {children}
      </a>
    );
  }
  return (
    <Link href={href} className={className}>
      {children}
    </Link>
  );
}

function ErrorBox({ message }: { message: string }) {
  return (
    <div className="bg-red-50 text-red-700 rounded-lg p-3 flex items-center gap-2 text-sm">
      <AlertCircle className="w-4 h-4 flex-shrink-0" /> {message}
    </div>
  );
}

interface AgentConfigPayload {
  account_id: string;
  api_key: string;
  rotated_at: string;
  hosted: {
    mcpServers: {
      settlebridge: {
        url: string;
        headers: { Authorization: string };
      };
    };
  };
  local: {
    mcpServers: {
      "a2a-settlement": {
        command: string;
        env: {
          SETTLEBRIDGE_GATEWAY_CLIENT_ID: string;
          SETTLEBRIDGE_GATEWAY_CLIENT_SECRET: string;
        };
      };
    };
  };
}

type ConfigTab = "hosted" | "local";

function readAgentConfig(data: unknown): AgentConfigPayload | null {
  if (!data || typeof data !== "object") return null;
  const body = data as AgentConfigPayload;
  if (!body.hosted || typeof body.hosted !== "object") return null;
  if (!body.local || typeof body.local !== "object") return null;
  return body;
}

/** 401 is left to the API client's existing sign-in redirect. */
function messageForAgentConfig(err: unknown): string | null {
  if (!axios.isAxiosError(err)) return "Couldn't create the agent config.";
  const status = err.response?.status;
  if (status === 401) return null;
  if (status === 409) {
    return "Your account has no exchange account yet. Finish GitHub sign-in first.";
  }
  if (status === 502) {
    return "The exchange did not respond. Your current key still works. Try again.";
  }
  return "Couldn't create the agent config.";
}

function AgentConfigCard({ needsConfirm, busy }: { needsConfirm: boolean; busy: boolean }) {
  const [payload, setPayload] = useState<AgentConfigPayload | null>(null);
  const [tab, setTab] = useState<ConfigTab>("hosted");
  const [confirming, setConfirming] = useState(false);
  const [creating, setCreating] = useState(false);
  const [configError, setConfigError] = useState("");
  const [copiedTab, setCopiedTab] = useState<ConfigTab | null>(null);
  const creatingRef = useRef(false);
  const copyTimer = useRef<number | null>(null);

  useEffect(() => {
    const clearOnLeave = () => setPayload(null);
    window.addEventListener("pagehide", clearOnLeave);
    return () => {
      window.removeEventListener("pagehide", clearOnLeave);
      if (copyTimer.current) window.clearTimeout(copyTimer.current);
      setPayload(null);
    };
  }, []);

  const clearConfig = () => {
    setPayload(null);
    setTab("hosted");
    setCopiedTab(null);
    setConfirming(false);
    setConfigError("");
    if (copyTimer.current) window.clearTimeout(copyTimer.current);
  };

  const createConfig = async () => {
    if (creatingRef.current) return;
    creatingRef.current = true;
    setCreating(true);
    setConfigError("");
    try {
      const { data } = await api.post<unknown>("/v1/github/solver/agent-config");
      const next = readAgentConfig(data);
      if (!next) {
        setConfigError("Couldn't create the agent config.");
        return;
      }
      setPayload(next);
      setTab("hosted");
      setCopiedTab(null);
      setConfirming(false);
    } catch (err: unknown) {
      const message = messageForAgentConfig(err);
      if (message) setConfigError(message);
    } finally {
      creatingRef.current = false;
      setCreating(false);
    }
  };

  const onCreateClick = () => {
    setConfigError("");
    if (needsConfirm) {
      setConfirming(true);
      return;
    }
    void createConfig();
  };

  const copyShown = (which: ConfigTab, text: string) => {
    navigator.clipboard.writeText(text).then(
      () => {
        setCopiedTab(which);
        if (copyTimer.current) window.clearTimeout(copyTimer.current);
        copyTimer.current = window.setTimeout(() => setCopiedTab(null), 2000);
      },
      () => setConfigError("Couldn't copy the MCP config.")
    );
  };

  const shownText = payload
    ? JSON.stringify(tab === "hosted" ? payload.hosted : payload.local, null, 2)
    : "";

  const tabClass = (which: ConfigTab) =>
    `flex-1 py-2 text-sm font-medium rounded-lg transition ${
      tab === which ? "bg-navy-900 text-white" : "text-gray-600 hover:text-navy-900"
    }`;

  return (
    <div className="bg-white border-2 border-navy-900 rounded-xl p-6 space-y-4">
      <div className="flex items-center gap-2">
        <Key className="w-4 h-4 text-navy-900" />
        <h2 className="font-semibold text-navy-900">Create agent config</h2>
      </div>
      <p className="text-sm text-gray-700">
        Creates a new exchange key and a config with it filled in. The config is shown once. Your
        previous key stops working, so update any agent already using it.
      </p>

      {configError && <ErrorBox message={configError} />}

      {payload ? (
        <div className="space-y-3">
          <div className="flex bg-gray-100 rounded-lg p-1" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "hosted"}
              className={tabClass("hosted")}
              onClick={() => setTab("hosted")}
            >
              Hosted (recommended)
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "local"}
              className={tabClass("local")}
              onClick={() => setTab("local")}
            >
              Local
            </button>
          </div>
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => copyShown(tab, shownText)}
              className="flex items-center gap-1 text-xs text-navy-700 hover:text-navy-900 font-medium"
            >
              {copiedTab === tab ? (
                <>
                  <Check className="w-3.5 h-3.5" /> Copied
                </>
              ) : (
                <>
                  <Copy className="w-3.5 h-3.5" /> Copy
                </>
              )}
            </button>
          </div>
          <pre className="text-xs font-mono bg-gray-50 border border-gray-200 rounded-lg p-3 overflow-x-auto text-gray-800">
            {shownText}
          </pre>
          <p className="text-sm text-gray-600">
            {tab === "hosted"
              ? "Paste into your MCP client config (Cursor: ~/.cursor/mcp.json). No install needed."
              : "Needs pip install a2a-settlement-mcp."}
          </p>
          <button
            type="button"
            onClick={clearConfig}
            className="inline-flex items-center justify-center px-5 py-2.5 border border-gray-300 text-navy-900 rounded-lg font-semibold text-sm hover:bg-gray-50 transition"
          >
            Done
          </button>
        </div>
      ) : confirming ? (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 space-y-3">
          <p className="text-sm text-amber-900">Replace your current key with a new one?</p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void createConfig()}
              disabled={creating}
              className="inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-navy-900 text-white rounded-lg font-semibold text-sm hover:bg-navy-800 transition disabled:opacity-50"
            >
              {creating && <Loader2 className="w-4 h-4 animate-spin" />}
              {creating ? "Creating…" : "Create agent config"}
            </button>
            <button
              type="button"
              onClick={() => setConfirming(false)}
              disabled={creating}
              className="px-4 py-2.5 bg-white text-gray-700 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={onCreateClick}
          disabled={busy || creating}
          className="inline-flex items-center justify-center gap-2 px-5 py-3 bg-navy-900 text-white rounded-lg font-semibold text-sm hover:bg-navy-800 transition disabled:opacity-50"
        >
          {creating && <Loader2 className="w-4 h-4 animate-spin" />}
          {creating ? "Creating…" : "Create agent config"}
        </button>
      )}
    </div>
  );
}

export default function SolverSetup() {
  const searchParams = useSearchParams();
  const welcome = searchParams.get("welcome") === "1";
  const [status, setStatus] = useState<SolverSetupStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .get<SolverSetupStatus>("/v1/github/solver/setup-status")
      .then(({ data }) => {
        if (!cancelled) setStatus(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(messageFrom(err, "Couldn't load solver setup."));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const configText = status ? JSON.stringify(status.mcp_config, null, 2) : "";

  const copyConfig = () => {
    if (!configText) return;
    navigator.clipboard.writeText(configText).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 2000);
      },
      () => setError("Couldn't copy the MCP config.")
    );
  };

  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-navy-900 flex items-center justify-center">
          <Wrench className="w-5 h-5 text-money" />
        </div>
        <div>
          <h1 className="font-semibold text-navy-900">Solver setup</h1>
          <p className="text-xs text-gray-500">
            Claim GitHub bounties from your agent with the settlement MCP server.
          </p>
        </div>
      </div>

      {welcome && (
        <p className="text-sm text-navy-900 bg-green-50 border border-green-200 rounded-lg px-4 py-3">
          Welcome. Create your agent config to start solving bounties.
        </p>
      )}

      <AgentConfigCard needsConfirm={!welcome && Boolean(status?.exchange_account_id)} busy={loading} />

      {loading && (
        <p className="text-sm text-gray-400 flex items-center gap-2">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading solver setup…
        </p>
      )}

      {error && <ErrorBox message={error} />}

      {status && (
        <div className="bg-white border border-gray-200 rounded-xl p-6 space-y-5">
          <p
            className={`text-sm rounded-lg p-3 flex items-center gap-2 ${
              status.ready ? "bg-green-50 text-green-700" : "bg-amber-50 text-amber-900"
            }`}
          >
            {status.ready ? (
              <CheckCircle className="w-4 h-4 flex-shrink-0" />
            ) : (
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
            )}
            {status.ready ? "Ready to claim bounties" : "Setup still needed"}
          </p>

          <dl className="grid sm:grid-cols-3 gap-4 text-sm">
            <div>
              <dt className="text-xs text-gray-500">Reputation</dt>
              <dd className="font-medium text-navy-900">{reputationText(status.reputation)}</dd>
            </div>
            <div>
              <dt className="text-xs text-gray-500">GitHub</dt>
              <dd className="font-medium text-navy-900">{githubText(status.github_login)}</dd>
            </div>
            <div>
              <dt className="text-xs text-gray-500">Financial scope</dt>
              <dd className="font-medium text-navy-900">
                {financialScopeLabel(status.financial_scope)}
              </dd>
            </div>
          </dl>

          {status.next_steps.length > 0 && (
            <ul className="space-y-2">
              {status.next_steps.map((step) => (
                <li
                  key={step.code}
                  className={`text-sm rounded-lg p-3 ${
                    step.blocking ? "bg-amber-50 text-amber-900" : "bg-gray-50 text-gray-700"
                  }`}
                >
                  {step.url ? (
                    <TextLink href={step.url} className="font-medium underline">
                      {step.message}
                    </TextLink>
                  ) : (
                    step.message
                  )}
                </li>
              ))}
            </ul>
          )}

          <div className="space-y-2">
            <h2 className="text-sm font-semibold text-navy-900">Eligible bounties</h2>
            {status.eligible_bounties.length === 0 ? (
              <p className="text-sm text-gray-600">No open bounties you can claim right now.</p>
            ) : (
              <ul className="space-y-2">
                {status.eligible_bounties.map((bounty) => (
                  <li key={bounty.bounty_id}>
                    <Link
                      href={`/bounties/${bounty.bounty_id}`}
                      className="block rounded-lg border border-gray-200 p-3 hover:bg-gray-50"
                    >
                      <span className="block font-medium text-navy-900">{bounty.title}</span>
                      <span className="block text-xs font-mono text-gray-500">
                        {bounty.repo}#{bounty.issue_number}
                      </span>
                      <span className="block text-sm text-gray-700 mt-1">
                        {bounty.amount_ate} ATE (test) · required reputation {bounty.required_ema}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            {status.ineligible_count > 0 && (
              <p className="text-sm text-gray-600">
                {status.ineligible_count} open bounties need a higher reputation.
              </p>
            )}
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold text-navy-900">MCP config</h2>
                <p className="text-xs text-gray-500">Uses a placeholder key.</p>
              </div>
              <button
                type="button"
                onClick={copyConfig}
                className="flex items-center gap-1 text-xs text-navy-700 hover:text-navy-900 font-medium flex-shrink-0"
              >
                {copied ? (
                  <>
                    <Check className="w-3.5 h-3.5" /> Copied
                  </>
                ) : (
                  <>
                    <Copy className="w-3.5 h-3.5" /> Copy
                  </>
                )}
              </button>
            </div>
            <pre className="text-xs font-mono bg-gray-50 border border-gray-200 rounded-lg p-3 overflow-x-auto text-gray-800">
              {configText}
            </pre>
            <p className="text-sm text-gray-600">
              The secret placeholder must be replaced with the exchange API key.{" "}
              <TextLink
                href={status.urls.rotate_key}
                className="font-medium text-navy-800 underline"
              >
                Rotate key in Settings
              </TextLink>
              {" · "}
              <TextLink
                href={status.urls.link_github}
                className="font-medium text-navy-800 underline"
              >
                Link GitHub
              </TextLink>
            </p>
          </div>

          <TextLink
            href={status.urls.bounties}
            className="inline-flex items-center justify-center px-5 py-3 bg-navy-900 text-white rounded-lg font-semibold text-sm hover:bg-navy-800 transition"
          >
            Browse bounties
          </TextLink>
        </div>
      )}
    </div>
  );
}
