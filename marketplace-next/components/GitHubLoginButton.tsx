"use client";

import { useState } from "react";
import axios from "axios";
import { GitBranch } from "lucide-react";
import { startGitHubLogin } from "@/lib/githubLogin";

export function GitHubLoginButton({ label = "Continue with GitHub" }: { label?: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const handleClick = async () => {
    setBusy(true);
    setError("");
    try {
      const next = new URLSearchParams(window.location.search).get("next");
      await startGitHubLogin(next);
    } catch (err: unknown) {
      const status = axios.isAxiosError(err) ? err.response?.status : undefined;
      setError(
        status === 404 || status === 503
          ? "Sign in with GitHub isn't available yet."
          : "Couldn't reach GitHub. Try again."
      );
      setBusy(false);
    }
  };

  return (
    <div className="mb-6">
      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        className="w-full flex items-center justify-center gap-2 py-2.5 bg-gray-900 text-white rounded-lg font-semibold text-sm hover:bg-black transition disabled:opacity-50"
      >
        <GitBranch className="w-4 h-4" />
        {busy ? "Opening GitHub..." : label}
      </button>
      {error && <p className="text-xs text-red-600 mt-2">{error}</p>}
      <div className="flex items-center gap-3 mt-6 text-xs text-gray-400">
        <span className="h-px flex-1 bg-gray-200" /> or <span className="h-px flex-1 bg-gray-200" />
      </div>
    </div>
  );
}
