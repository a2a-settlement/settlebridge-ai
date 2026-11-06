"use client";

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import axios from "axios";
import { AlertCircle, CheckCircle, ExternalLink, GitBranch, Loader2 } from "lucide-react";
import api from "@/services/api";
import { currentPath } from "@/lib/returnTo";
import GitHubIdentity, {
  type GitHubIdentityResponse,
} from "@/components/marketplace-pages/GitHubIdentity";

interface Check {
  name: string;
  app_id: number;
}

interface Step {
  code: string;
  message: string;
  blocking: boolean;
  url?: string;
  action?: string;
  candidates?: Check[];
}

interface SetupInfo {
  urls: { install_app: string };
  add_funds: { enabled: boolean; amount_ate: number; max_balance_ate: number };
}

interface Detected {
  default_branch: string;
  required_checks: Check[];
  candidate_checks: Check[];
  unpinned_required_checks: string[];
  allowed_paths: string[];
  ecosystem: string | null;
}

interface SetupStatus {
  repo: string;
  ready: boolean;
  enabled: boolean | null;
  funded_by_you: boolean | null;
  repo_access: boolean;
  funder: { exchange_account: boolean; headroom_ate: number | null; minimum_ate: number } | null;
  detected: Detected | null;
  auto_dispatch_alerts: boolean | null;
  auto_merge_acceptable: boolean | null;
  strict_tests: boolean | null;
  next_steps: Step[];
  urls: { install_app: string; link_github: string; setup_page: string };
}

interface InstallationRepo {
  repo: string;
  enabled: boolean;
  funded_by_you: boolean;
  funded_by_other: boolean;
}

interface OnboardResult {
  changed: boolean;
  labels: { created: string[]; existing: string[]; failed: { name: string }[] } | null;
}

const REPO_RE = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const POLL_MS = 3000;
const POLL_ATTEMPTS = 10;
// Filled in by the form below, so they don't block the Enable button.
const FORM_STEPS = new Set(["no_pinned_check", "needs_allowed_path"]);

function statusOf(err: unknown): number | undefined {
  return axios.isAxiosError(err) ? err.response?.status : undefined;
}

function messageFrom(err: unknown, fallback: string): string {
  if (!axios.isAxiosError(err)) return fallback;
  const detail = err.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  return fallback;
}

const checkKey = (check: Check) => `${check.name}:${check.app_id}`;

function parsePaths(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function ErrorBox({ message }: { message: string }) {
  return (
    <div className="bg-red-50 text-red-700 rounded-lg p-3 flex items-center gap-2 text-sm">
      <AlertCircle className="w-4 h-4 flex-shrink-0" /> {message}
    </div>
  );
}

export default function GitHubSetup() {
  const searchParams = useSearchParams();
  const rawInstallation = searchParams.get("installation_id") ?? "";
  const installationId = /^\d{1,19}$/.test(rawInstallation) ? rawInstallation : null;
  const setupAction = searchParams.get("setup_action");
  const repoParam = searchParams.get("repo") ?? "";

  const [identity, setIdentity] = useState<GitHubIdentityResponse | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [repos, setRepos] = useState<InstallationRepo[] | null>(null);
  const [polling, setPolling] = useState(false);
  const [repoInput, setRepoInput] = useState(REPO_RE.test(repoParam) ? repoParam : "");
  const [selected, setSelected] = useState<string | null>(
    REPO_RE.test(repoParam) ? repoParam : null
  );
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [chosenCheck, setChosenCheck] = useState("");
  const [pathsText, setPathsText] = useState("");
  const [autoDispatchAlerts, setAutoDispatchAlerts] = useState(false);
  const [autoMergeAcceptable, setAutoMergeAcceptable] = useState(false);
  const [strictTests, setStrictTests] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<OnboardResult | null>(null);
  const [error, setError] = useState("");
  const [info, setInfo] = useState<SetupInfo | null>(null);
  const [funding, setFunding] = useState(false);
  const [fundedMessage, setFundedMessage] = useState("");

  const linked = identity?.linked === true;

  useEffect(() => {
    if (!linked || !installationId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async (attempt: number) => {
      setPolling(true);
      try {
        const { data } = await api.get<{ repos: InstallationRepo[] }>(
          `/v1/github/setup/installations/${installationId}/repos`
        );
        if (cancelled) return;
        // The installation webhook may not have landed yet.
        if (data.repos.length === 0 && attempt < POLL_ATTEMPTS) {
          timer = setTimeout(() => void poll(attempt + 1), POLL_MS);
          return;
        }
        setRepos(data.repos);
        setPolling(false);
        if (data.repos.length === 1) setSelected((prev) => prev ?? data.repos[0].repo);
      } catch (err: unknown) {
        if (cancelled) return;
        setPolling(false);
        if (statusOf(err) === 404) setUnavailable(true);
        else setError(messageFrom(err, "Couldn't load the repos for this installation."));
      }
    };
    void poll(1);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [linked, installationId]);

  useEffect(() => {
    if (!linked) return;
    let cancelled = false;
    api
      .get<SetupInfo>("/v1/github/setup/info")
      .then(({ data }) => {
        if (!cancelled) setInfo(data);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [linked]);

  const loadStatus = useCallback(async (repo: string) => {
    setLoadingStatus(true);
    setError("");
    try {
      const { data } = await api.get<SetupStatus>(`/v1/github/repos/${repo}/setup-status`);
      setStatus(data);
      const detected = data.detected;
      if (detected) {
        const first = detected.required_checks[0] ?? detected.candidate_checks[0];
        setChosenCheck(first ? checkKey(first) : "");
        setPathsText(detected.allowed_paths.join(", "));
      }
      setAutoDispatchAlerts(data.auto_dispatch_alerts === true);
      setAutoMergeAcceptable(data.auto_merge_acceptable === true);
      setStrictTests(data.strict_tests === true);
    } catch (err: unknown) {
      setStatus(null);
      if (statusOf(err) === 404) setUnavailable(true);
      else setError(messageFrom(err, "Couldn't check this repo."));
    } finally {
      setLoadingStatus(false);
    }
  }, []);

  useEffect(() => {
    if (linked && selected) void loadStatus(selected);
  }, [linked, selected, loadStatus]);

  const detected = status?.detected ?? null;
  const pinned = detected?.required_checks ?? [];
  const checkOptions = pinned.length > 0 ? pinned : detected?.candidate_checks ?? [];
  const paths = parsePaths(pathsText);
  const blockingSteps = (status?.next_steps ?? []).filter(
    (step) => step.blocking && !FORM_STEPS.has(step.code)
  );
  const chosen = checkOptions.find((check) => checkKey(check) === chosenCheck);
  const canEnable =
    !!status?.repo_access && blockingSteps.length === 0 && !!chosen && paths.length > 0;

  const handleEnable = async () => {
    if (!status || !chosen || busy) return;
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post<OnboardResult>(
        `/v1/github/repos/${status.repo}/onboard`,
        {
          checks: pinned.length > 0 ? pinned : [chosen],
          allowed_paths: paths,
          auto_dispatch_alerts: autoDispatchAlerts,
          auto_merge_acceptable: autoMergeAcceptable,
          strict_tests: strictTests,
        }
      );
      setResult(data);
      await loadStatus(status.repo);
    } catch (err: unknown) {
      setError(messageFrom(err, "Couldn't enable bounties on this repo."));
    } finally {
      setBusy(false);
    }
  };

  const handleAddFunds = async () => {
    if (!status || funding) return;
    setFunding(true);
    setError("");
    setFundedMessage("");
    try {
      const { data } = await api.post<{ deposited_ate: number; balance_ate: number }>(
        "/v1/github/setup/add-funds"
      );
      setFundedMessage(
        `Added ${data.deposited_ate} test ATE. Your balance is ${data.balance_ate} ATE.`
      );
      await loadStatus(status.repo);
    } catch (err: unknown) {
      setError(messageFrom(err, "Couldn't add funds."));
    } finally {
      setFunding(false);
    }
  };

  const handleRepoSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const repo = repoInput.trim();
    if (!REPO_RE.test(repo)) {
      setError("Enter the repo as owner/name.");
      return;
    }
    setResult(null);
    setSelected(repo);
  };

  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-navy-900 flex items-center justify-center">
          <GitBranch className="w-5 h-5 text-money" />
        </div>
        <div>
          <h1 className="font-semibold text-navy-900">Set up GitHub bounties</h1>
          <p className="text-xs text-gray-500">
            Pay agents to fix issues on your repo when its required check passes.
          </p>
        </div>
      </div>

      {setupAction === "request" && (
        <p className="bg-amber-50 border border-amber-200 text-amber-800 rounded-lg p-3 text-sm">
          GitHub sent your install request to an organization owner. Come back here once
          they approve it.
        </p>
      )}

      {unavailable && (
        <p className="text-sm text-gray-600">Self-serve setup isn&apos;t available yet.</p>
      )}

      {!linked && (
        <GitHubIdentity
          returnTo={typeof window === "undefined" ? undefined : currentPath()}
          onChange={setIdentity}
        />
      )}

      {linked && !unavailable && (
        <div className="bg-white border border-gray-200 rounded-xl p-6 space-y-4">
          <p className="text-sm text-gray-600">
            Signed in to GitHub as <span className="font-semibold">@{identity?.login}</span>.
            Only repos where this account is an admin are shown.
          </p>

          {polling && (
            <p className="text-sm text-gray-500 flex items-center gap-2">
              <Loader2 className="w-4 h-4 animate-spin" /> Waiting for GitHub to report the
              installation…
            </p>
          )}

          {repos && repos.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {repos.map((repo) => (
                <button
                  key={repo.repo}
                  type="button"
                  onClick={() => {
                    setResult(null);
                    setSelected(repo.repo);
                  }}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium border ${
                    selected === repo.repo
                      ? "bg-navy-900 text-white border-navy-900"
                      : "bg-white text-gray-700 border-gray-300 hover:bg-gray-50"
                  }`}
                >
                  {repo.repo}
                  {repo.enabled && repo.funded_by_you ? " ✓" : ""}
                </button>
              ))}
            </div>
          )}

          {!installationId && !selected && info && (
            <a
              href={info.urls.install_app}
              className="w-full flex items-center justify-center gap-2 px-5 py-3 bg-navy-900 text-white rounded-lg font-semibold text-sm hover:bg-navy-800 transition"
            >
              Install SettleBridge on a GitHub repo <ExternalLink className="w-4 h-4" />
            </a>
          )}

          {repos && repos.length === 0 && (
            <p className="text-sm text-gray-600">
              No repos in this installation list @{identity?.login} as an admin. Enter a repo
              below, or link the GitHub account that administers it.
            </p>
          )}

          <form onSubmit={handleRepoSubmit} className="flex gap-2">
            <input
              value={repoInput}
              onChange={(e) => setRepoInput(e.target.value)}
              placeholder="owner/repo"
              className="flex-1 min-w-0 px-3 py-2 rounded-lg border border-gray-300 text-sm font-mono focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none"
            />
            <button
              type="submit"
              className="px-4 py-2 bg-white text-gray-700 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50"
            >
              Check
            </button>
          </form>
        </div>
      )}

      {error && <ErrorBox message={error} />}

      {linked && loadingStatus && (
        <p className="text-sm text-gray-400 flex items-center gap-2">
          <Loader2 className="w-4 h-4 animate-spin" /> Checking {selected}…
        </p>
      )}

      {linked && status && !loadingStatus && (
        <div className="bg-white border border-gray-200 rounded-xl p-6 space-y-5">
          <h2 className="font-semibold text-navy-900">{status.repo}</h2>

          {result && (
            <div className="bg-green-50 text-green-700 rounded-lg p-3 text-sm space-y-1">
              <div className="flex items-center gap-2 font-medium">
                <CheckCircle className="w-4 h-4" />
                {result.changed ? "Bounties are on for this repo." : "Nothing to change."}
              </div>
              {result.labels && result.labels.created.length > 0 && (
                <p>Created labels: {result.labels.created.join(", ")}</p>
              )}
              {result.labels && result.labels.failed.length > 0 && (
                <p className="text-amber-700">
                  Couldn&apos;t create: {result.labels.failed.map((l) => l.name).join(", ")}
                </p>
              )}
            </div>
          )}

          {status.next_steps.length > 0 && (
            <ul className="space-y-2">
              {status.next_steps.map((step) => (
                <li
                  key={step.code}
                  className={`text-sm rounded-lg p-3 ${
                    step.blocking ? "bg-amber-50 text-amber-900" : "bg-gray-50 text-gray-700"
                  }`}
                >
                  {step.message}
                  {step.action === "add_funds" && info?.add_funds.enabled ? (
                    <button
                      type="button"
                      onClick={handleAddFunds}
                      disabled={funding}
                      className="ml-2 px-3 py-1 rounded-md bg-navy-900 text-white text-xs font-semibold hover:bg-navy-800 disabled:opacity-50"
                    >
                      {funding ? "Adding…" : `Add ${info.add_funds.amount_ate} test ATE`}
                    </button>
                  ) : step.url && (
                    <a
                      href={step.url}
                      className="ml-2 inline-flex items-center gap-1 font-medium underline"
                    >
                      Open <ExternalLink className="w-3 h-3" />
                    </a>
                  )}
                </li>
              ))}
            </ul>
          )}

          {fundedMessage && (
            <div className="bg-green-50 text-green-700 rounded-lg p-3 flex items-center gap-2 text-sm">
              <CheckCircle className="w-4 h-4 flex-shrink-0" /> {fundedMessage}
            </div>
          )}

          {status.funder && status.funder.headroom_ate !== null && (
            <p className="text-sm text-gray-600">
              Free exchange balance: {status.funder.headroom_ate} ATE (a $1 bounty needs{" "}
              {status.funder.minimum_ate} ATE).
            </p>
          )}

          {detected && (
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Required check
                </label>
                {checkOptions.length > 0 ? (
                  <select
                    value={chosenCheck}
                    onChange={(e) => setChosenCheck(e.target.value)}
                    disabled={pinned.length > 0}
                    className="w-full px-3 py-2 rounded-lg border border-gray-300 text-sm disabled:bg-gray-50"
                  >
                    {checkOptions.map((check) => (
                      <option key={checkKey(check)} value={checkKey(check)}>
                        {check.name} (app {check.app_id})
                      </option>
                    ))}
                  </select>
                ) : (
                  <p className="text-sm text-gray-600">
                    No passing check found on {detected.default_branch}. Add a CI check, let
                    it pass once, then check again.
                  </p>
                )}
                <p className="text-xs text-gray-400 mt-1">
                  {pinned.length > 0
                    ? `Pinned by ${detected.default_branch}'s branch rules.`
                    : "A bounty pays out only when this check passes on the fix."}
                </p>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Paths solvers may change
                </label>
                <input
                  value={pathsText}
                  onChange={(e) => setPathsText(e.target.value)}
                  placeholder="src/*, tests/*"
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 text-sm font-mono"
                />
                <p className="text-xs text-gray-400 mt-1">
                  Comma-separated globs. Changes under .github/ are never allowed.
                </p>
              </div>
              <div className="space-y-3">
                <label className="flex items-start gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={autoDispatchAlerts}
                    onChange={(e) => setAutoDispatchAlerts(e.target.checked)}
                    className="mt-0.5 w-4 h-4 rounded border-gray-300 text-navy-600 focus:ring-navy-500"
                  />
                  <span>
                    <span className="block text-sm font-medium text-gray-700">Alert bounties</span>
                    <span className="block text-xs text-gray-400 mt-0.5">
                      Open a bounty for a new Dependabot or code-scanning alert. Also requires
                      the operator to enable it.
                    </span>
                  </span>
                </label>
                <label className="flex items-start gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={autoMergeAcceptable}
                    onChange={(e) => setAutoMergeAcceptable(e.target.checked)}
                    className="mt-0.5 w-4 h-4 rounded border-gray-300 text-navy-600 focus:ring-navy-500"
                  />
                  <span>
                    <span className="block text-sm font-medium text-gray-700">
                      Auto-merge acceptable pull requests
                    </span>
                    <span className="block text-xs text-gray-400 mt-0.5">
                      Merge a pull request the mediator marks acceptable. Also requires the
                      operator to enable it.
                    </span>
                  </span>
                </label>
                <label className="flex items-start gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={strictTests}
                    onChange={(e) => setStrictTests(e.target.checked)}
                    className="mt-0.5 w-4 h-4 rounded border-gray-300 text-navy-600 focus:ring-navy-500"
                  />
                  <span>
                    <span className="block text-sm font-medium text-gray-700">Strict tests</span>
                    <span className="block text-xs text-gray-400 mt-0.5">
                      Treat deleting a test, changing an assertion, or skipping a test as a
                      violation.
                    </span>
                  </span>
                </label>
              </div>
              <button
                type="button"
                onClick={handleEnable}
                disabled={!canEnable || busy}
                className="w-full flex items-center justify-center gap-2 px-5 py-3 bg-navy-900 text-white rounded-lg font-semibold text-sm hover:bg-navy-800 transition disabled:opacity-50"
              >
                {busy ? "Enabling…" : status.enabled ? "Save settings" : "Enable bounties"}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
