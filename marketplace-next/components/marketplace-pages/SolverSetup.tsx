"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import axios from "axios";
import { AlertCircle, Check, CheckCircle, Copy, Loader2, Wrench } from "lucide-react";
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

export default function SolverSetup() {
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
                        {bounty.amount_ate} ATE · required reputation {bounty.required_ema}
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
              <h2 className="text-sm font-semibold text-navy-900">MCP config</h2>
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
