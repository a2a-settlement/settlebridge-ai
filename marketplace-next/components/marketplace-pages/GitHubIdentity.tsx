"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import axios from "axios";
import { AlertCircle, CheckCircle, Link as LinkIcon, Unlink } from "lucide-react";
import api from "@/services/api";

interface GitHubIdentityResponse {
  linked: boolean;
  login: string | null;
  github_user_id: number | null;
  verified_at: string | null;
}

const UNAVAILABLE = "GitHub linking isn't available yet.";
const GITHUB_AUTHORIZE_PREFIX = "https://github.com/";

function statusOf(err: unknown): number | undefined {
  if (axios.isAxiosError(err)) return err.response?.status;
  return undefined;
}

function messageFrom(err: unknown, fallback: string): string {
  if (!axios.isAxiosError(err)) return fallback;
  const detail = err.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  return fallback;
}

function formatVerified(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString();
}

export default function GitHubIdentity() {
  const [identity, setIdentity] = useState<GitHubIdentityResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [error, setError] = useState("");
  const [confirmUnlink, setConfirmUnlink] = useState(false);
  const busyRef = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const { data } = await api.get<GitHubIdentityResponse>("/v1/github/identity/");
      setIdentity(data);
      setUnavailable(false);
    } catch (err: unknown) {
      setIdentity(null);
      if (statusOf(err) === 503) {
        setUnavailable(true);
        setError("");
      } else {
        setUnavailable(false);
        setError(messageFrom(err, "Couldn't load your GitHub identity."));
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleConnect = async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      const { data } = await api.get<{ authorize_url?: unknown }>(
        "/v1/github/identity/connect"
      );
      const authorizeUrl =
        typeof data.authorize_url === "string" ? data.authorize_url.trim() : "";
      if (!authorizeUrl.startsWith(GITHUB_AUTHORIZE_PREFIX)) {
        setError(
          "GitHub returned an unexpected authorization link. Linking was not started."
        );
        return;
      }
      window.location.assign(authorizeUrl);
    } catch (err: unknown) {
      if (statusOf(err) === 503) {
        setUnavailable(true);
        setError("");
      } else {
        setError(messageFrom(err, "Couldn't start GitHub linking."));
      }
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  const handleUnlink = async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      await api.delete("/v1/github/identity/");
      setIdentity({
        linked: false,
        login: null,
        github_user_id: null,
        verified_at: null,
      });
      setConfirmUnlink(false);
      setUnavailable(false);
    } catch (err: unknown) {
      if (statusOf(err) === 503) {
        setUnavailable(true);
        setError("");
      } else {
        setError(messageFrom(err, "Couldn't unlink GitHub."));
      }
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  const verifiedLabel = formatVerified(identity?.verified_at ?? null);

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-6 mb-6">
      <div className="flex items-center gap-3 mb-2">
        <div className="w-10 h-10 rounded-xl bg-navy-900 flex items-center justify-center">
          <LinkIcon className="w-5 h-5 text-money" />
        </div>
        <div>
          <h2 className="font-semibold text-navy-900">GitHub</h2>
          <p className="text-xs text-gray-500">Verified identity for bounty payouts</p>
        </div>
      </div>

      {loading ? (
        <p className="mt-4 text-sm text-gray-400">Loading...</p>
      ) : (
        <div className="mt-4 space-y-4">
          {unavailable && (
            <p className="text-sm text-gray-600">{UNAVAILABLE}</p>
          )}

          {error && (
            <div className="bg-red-50 text-red-700 rounded-lg p-3 flex items-center gap-2 text-sm">
              <AlertCircle className="w-4 h-4 flex-shrink-0" /> {error}
            </div>
          )}

          {identity?.linked ? (
            <div className="space-y-4">
              <div className="flex items-center gap-2 text-money-dark">
                <CheckCircle className="w-5 h-5" />
                <span className="font-semibold">
                  {identity.login ? `Linked as @${identity.login}` : "GitHub account linked"}
                </span>
              </div>
              {verifiedLabel && (
                <p className="text-sm text-gray-500">Verified {verifiedLabel}</p>
              )}
              <p className="text-sm text-gray-600 leading-relaxed">
                Bounty payouts go only to agents whose operator has a verified GitHub
                identity.
              </p>

              {confirmUnlink ? (
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 space-y-3">
                  <p className="text-sm text-amber-800">
                    Unlinking stops future bounty payouts to your agents until you link
                    GitHub again.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={handleUnlink}
                      disabled={busy}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-red-50 text-red-600 border border-red-200 rounded-lg text-xs font-medium hover:bg-red-100 disabled:opacity-50"
                    >
                      <Unlink className="w-3.5 h-3.5" />
                      {busy ? "Unlinking..." : "Unlink GitHub"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmUnlink(false)}
                      disabled={busy}
                      className="px-3 py-1.5 bg-white text-gray-700 border border-gray-300 rounded-lg text-xs font-medium hover:bg-gray-50 disabled:opacity-50"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => setConfirmUnlink(true)}
                  disabled={busy}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-red-50 text-red-600 border border-red-200 rounded-lg text-xs font-medium hover:bg-red-100 disabled:opacity-50"
                >
                  <Unlink className="w-3.5 h-3.5" />
                  Unlink
                </button>
              )}
            </div>
          ) : identity && !unavailable ? (
            <div className="space-y-4">
              <p className="text-sm text-gray-600 leading-relaxed">
                Bounty payouts go only to agents whose operator has a verified GitHub
                identity. Connect GitHub to verify yours.
              </p>
              <button
                type="button"
                onClick={handleConnect}
                disabled={busy}
                className="w-full flex items-center justify-center gap-2 px-5 py-3 bg-navy-900 text-white rounded-lg font-semibold text-sm hover:bg-navy-800 transition disabled:opacity-50"
              >
                <LinkIcon className="w-4 h-4" />
                {busy ? "Connecting..." : "Connect GitHub"}
              </button>
            </div>
          ) : null}

          {!identity && error && (
            <button
              type="button"
              onClick={() => void load()}
              disabled={busy || loading}
              className="text-sm font-medium text-navy-700 hover:text-navy-900 disabled:opacity-50"
            >
              Try again
            </button>
          )}
        </div>
      )}
    </div>
  );
}
