"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import axios from "axios";
import { AlertCircle, ArrowLeft, CheckCircle, Link as LinkIcon } from "lucide-react";
import api from "@/services/api";
import { useAuth } from "@/hooks/useAuth";
import { isGitHubLoginState, takeGitHubLoginNonce } from "@/lib/githubLogin";
import { takeReturnPath } from "@/lib/returnTo";

type Phase = "working" | "linked" | "signed_in" | "unavailable" | "failed";

const UNAVAILABLE = "GitHub linking isn't available yet.";

function failureMessage(
  oauthError: string | null,
  code: string | null,
  state: string | null
): string {
  if (oauthError === "access_denied") return "GitHub linking was cancelled.";
  if (oauthError) return "GitHub did not complete linking.";
  if (!code || !state) {
    return "This page is missing the details needed to finish linking GitHub.";
  }
  return "";
}

const SOLVER_WELCOME = "/dashboard/github/solver?welcome=1";

/** Repo setup keeps its own return path. Any other new GitHub account starts as a solver. */
function isRepoSetupReturn(path: string | null): boolean {
  if (!path) return false;
  const end = path.search(/[?#]/);
  const pathname = end === -1 ? path : path.slice(0, end);
  return pathname === "/dashboard/github/setup" || pathname.startsWith("/dashboard/github/setup/");
}

function destinationAfterGitHubSignIn(
  created: boolean,
  returnedNext: string | null,
  storedReturn: string | null
): string {
  if (created && !isRepoSetupReturn(returnedNext)) return SOLVER_WELCOME;
  return returnedNext ?? storedReturn ?? "/dashboard";
}

function stripOAuthParams() {
  const url = new URL(window.location.href);
  if (!url.searchParams.has("code") && !url.searchParams.has("state")) return;
  url.searchParams.delete("code");
  url.searchParams.delete("state");
  window.history.replaceState(
    window.history.state,
    "",
    `${url.pathname}${url.search}${url.hash}`
  );
}

export default function GitHubCallback() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const code = searchParams.get("code");
  const state = searchParams.get("state");
  const oauthError = searchParams.get("error");
  const { githubLogin } = useAuth();
  const signingIn = isGitHubLoginState(state);
  // The authorization code is single-use. Strict Mode runs this effect twice.
  const posted = useRef(false);

  const [phase, setPhase] = useState<Phase>(() =>
    oauthError || !code || !state ? "failed" : "working"
  );
  const [message, setMessage] = useState(() => failureMessage(oauthError, code, state));
  const [login, setLogin] = useState<string | null>(null);
  const [returnPath, setReturnPath] = useState<string | null>(null);

  useEffect(() => {
    stripOAuthParams();
    if (posted.current) return;
    posted.current = true;
    const next = takeReturnPath();
    setReturnPath(next);

    if (oauthError || !code || !state) {
      setPhase("failed");
      setMessage(failureMessage(oauthError, code, state));
      return;
    }

    const authorizationCode = code;
    const authorizationState = state;

    if (signingIn) {
      const nonce = takeGitHubLoginNonce();
      if (!nonce) {
        setPhase("failed");
        setMessage("Sign-in was started in a different tab or browser. Start again from the sign-in page.");
        return;
      }
      void (async () => {
        try {
          const result = await githubLogin(authorizationCode, authorizationState, nonce);
          setLogin(result.login);
          setPhase("signed_in");
          router.replace(
            destinationAfterGitHubSignIn(result.created === true, result.next, next)
          );
        } catch (err: unknown) {
          const status = axios.isAxiosError(err) ? err.response?.status : undefined;
          setPhase(status === 404 || status === 503 ? "unavailable" : "failed");
          setMessage(
            status === 404 || status === 503
              ? "Sign in with GitHub isn't available yet."
              : "GitHub sign-in failed. Start again from the sign-in page."
          );
        }
      })();
      return;
    }

    void (async () => {
      try {
        const { data } = await api.post<{ login?: string | null }>(
          "/v1/github/identity/callback",
          { code: authorizationCode, state: authorizationState }
        );
        setLogin(typeof data.login === "string" ? data.login : null);
        setPhase("linked");
        if (next) router.replace(next);
      } catch (err: unknown) {
        const status = axios.isAxiosError(err) ? err.response?.status : undefined;
        if (status === 503) {
          setPhase("unavailable");
          setMessage(UNAVAILABLE);
          return;
        }
        if (status === 401) {
          setPhase("failed");
          setMessage("Sign in to SettleBridge first, then link GitHub again.");
          return;
        }
        if (status === 409) {
          setPhase("failed");
          setMessage(
            "That GitHub account is already linked to another SettleBridge user."
          );
          return;
        }
        if (status === 400) {
          setPhase("failed");
          setMessage(
            "Linking failed. The authorization code or state was invalid, expired, or issued for a different signed-in user."
          );
          return;
        }
        setPhase("failed");
        setMessage("Couldn't finish linking GitHub.");
      }
    })();
  }, [code, githubLogin, oauthError, router, signingIn, state]);

  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <div className="bg-white border border-gray-200 rounded-xl p-6">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-xl bg-navy-900 flex items-center justify-center">
            <LinkIcon className="w-5 h-5 text-money" />
          </div>
          <h1 className="font-semibold text-navy-900">
            {signingIn ? "Sign in with GitHub" : "Link GitHub"}
          </h1>
        </div>

        {phase === "working" && (
          <p className="text-sm text-gray-600">
            {signingIn ? "Signing you in..." : "Linking your GitHub account..."}
          </p>
        )}

        {phase === "linked" && (
          <div className="bg-green-50 text-green-700 rounded-lg p-3 flex items-center gap-2 text-sm">
            <CheckCircle className="w-4 h-4 flex-shrink-0" />
            <span>{login ? `Linked as @${login}` : "GitHub account linked."}</span>
          </div>
        )}

        {phase === "signed_in" && (
          <div className="bg-green-50 text-green-700 rounded-lg p-3 flex items-center gap-2 text-sm">
            <CheckCircle className="w-4 h-4 flex-shrink-0" />
            <span>{login ? `Signed in as @${login}` : "Signed in."}</span>
          </div>
        )}

        {phase === "unavailable" && (
          <p className="text-sm text-gray-600">{message || UNAVAILABLE}</p>
        )}

        {phase === "failed" && (
          <div className="bg-red-50 text-red-700 rounded-lg p-3 flex items-center gap-2 text-sm">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            {message}
          </div>
        )}

        {phase === "failed" && signingIn && (
          <Link
            href="/login"
            className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-navy-700 mt-4"
          >
            <ArrowLeft className="w-4 h-4" /> Back to sign in
          </Link>
        )}

        {phase !== "working" && phase !== "signed_in" && !(phase === "failed" && signingIn) && (
          <Link
            href={returnPath ?? "/dashboard/settings"}
            className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-navy-700 mt-4"
          >
            <ArrowLeft className="w-4 h-4" /> {returnPath ? "Continue" : "Back to settings"}
          </Link>
        )}
      </div>
    </div>
  );
}
