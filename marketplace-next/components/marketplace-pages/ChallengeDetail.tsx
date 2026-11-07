"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import axios from "axios";
import { ArrowLeft } from "lucide-react";
import { ChallengeResult } from "@/components/ChallengeResult";
import { useAuth } from "@/hooks/useAuth";
import type { ChallengeDetail as ChallengeDetailData } from "@/lib/cyberContracts";
import api from "@/services/api";

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-money rounded-sm";

type Phase = "loading" | "ready" | "missing" | "error";

function routeId(value: string | string[] | undefined): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value) && typeof value[0] === "string") return value[0];
  return "";
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6 lg:px-8">
      <Link
        href="/dashboard"
        className={`inline-flex items-center gap-1 text-sm text-gray-500 hover:text-navy-700 ${focusRing}`}
      >
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        Back to dashboard
      </Link>
      {children}
    </div>
  );
}

export default function ChallengeDetail() {
  const params = useParams();
  const id = routeId(params.id);
  const { user, loading: authLoading } = useAuth();
  const [challenge, setChallenge] = useState<ChallengeDetailData | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (authLoading) return;
    if (!user) {
      setPhase("loading");
      return;
    }
    if (!id) {
      setChallenge(null);
      setPhase("missing");
      return;
    }

    let cancelled = false;
    setPhase("loading");
    api
      .get<ChallengeDetailData>(`/v1/challenges/${encodeURIComponent(id)}`)
      .then(({ data }) => {
        if (cancelled) return;
        setChallenge(data);
        setPhase("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setChallenge(null);
        if (axios.isAxiosError(err) && err.response?.status === 401) {
          setPhase("loading");
          return;
        }
        if (axios.isAxiosError(err) && err.response?.status === 404) {
          setPhase("missing");
          return;
        }
        setPhase("error");
      });

    return () => {
      cancelled = true;
    };
  }, [authLoading, user, id, reloadKey]);

  if (phase === "loading") {
    return (
      <Frame>
        <div className="mt-6 animate-pulse" aria-busy="true" aria-live="polite">
          <p className="text-sm text-gray-500">Loading challenge</p>
          <div className="mt-4 h-8 w-1/2 rounded bg-gray-200" />
          <div className="mt-4 h-40 rounded bg-gray-200" />
        </div>
      </Frame>
    );
  }

  if (phase === "missing" || (phase === "ready" && !challenge)) {
    return (
      <Frame>
        <h1 className="mt-6 text-xl font-semibold text-navy-900">Challenge not found</h1>
      </Frame>
    );
  }

  if (phase === "error" || !challenge) {
    return (
      <Frame>
        <h1 className="mt-6 text-xl font-semibold text-navy-900">Challenge</h1>
        <p role="alert" className="mt-3 text-sm text-danger">
          The challenge could not be loaded.
        </p>
        <button
          type="button"
          onClick={() => setReloadKey((key) => key + 1)}
          className={`mt-3 text-sm font-medium text-navy-700 hover:text-navy-900 ${focusRing}`}
        >
          Try again
        </button>
      </Frame>
    );
  }

  const pullRequest = challenge.pr_number == null ? "" : ` · Pull request ${challenge.pr_number}`;

  return (
    <Frame>
      <h1 className="mt-6 text-2xl font-bold text-navy-900">Challenge</h1>
      <p className="mt-1 break-words text-sm text-gray-600">
        <span className="font-medium text-navy-900">{challenge.repo}</span>
        {pullRequest}
      </p>
      <div className="mt-8">
        <ChallengeResult challenge={challenge} />
      </div>
    </Frame>
  );
}
