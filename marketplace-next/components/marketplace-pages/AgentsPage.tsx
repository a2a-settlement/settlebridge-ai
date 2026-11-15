"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/hooks/useAuth";
import { useAppConfig } from "@/hooks/useAppConfig";
import { useManagedAgents } from "@/hooks/useManagedAgents";
import AgentDirectory from "@/components/marketplace-pages/AgentDirectory";
import MyAgents from "@/components/agents/MyAgents";

type AgentsTab = "mine" | "directory";

function parseTab(raw: string | null, signedIn: boolean): AgentsTab {
  if (raw === "directory") return "directory";
  if (raw === "mine") return "mine";
  return signedIn ? "mine" : "directory";
}

export default function AgentsPage() {
  const { agent_management_v2 } = useAppConfig();
  const { user } = useAuth();
  const searchParams = useSearchParams();
  const router = useRouter();

  const tab = useMemo(
    () => parseTab(searchParams.get("tab"), !!user),
    [searchParams, user]
  );

  const linked = !!user?.exchange_bot_id;
  const { ownedIds } = useManagedAgents(
    agent_management_v2 && tab === "directory" && linked
  );

  const setTab = useCallback(
    (next: AgentsTab) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set("tab", next);
      router.replace(`/agents?${params.toString()}`, { scroll: false });
    },
    [router, searchParams]
  );

  useEffect(() => {
    if (!agent_management_v2) return;
    const raw = searchParams.get("tab");
    if (raw === "mine" || raw === "directory") return;
    const defaultTab: AgentsTab = user ? "mine" : "directory";
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", defaultTab);
    router.replace(`/agents?${params.toString()}`, { scroll: false });
  }, [agent_management_v2, user, searchParams, router]);

  if (!agent_management_v2) {
    return <AgentDirectory />;
  }

  const tabClass = (active: boolean) =>
    `px-4 py-2 rounded-lg text-sm font-medium transition ${
      active
        ? "bg-navy-900 text-white"
        : "text-gray-600 hover:bg-gray-100"
    }`;

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-navy-900">Agents</h1>
        <p className="text-gray-500 text-sm mt-1">
          Manage your agents or browse the public directory
        </p>
      </div>

      <div
        className="inline-flex gap-1 p-1 bg-gray-100 rounded-xl mb-8"
        role="tablist"
        aria-label="Agents sections"
      >
        <button
          type="button"
          role="tab"
          aria-selected={tab === "mine"}
          className={tabClass(tab === "mine")}
          onClick={() => setTab("mine")}
        >
          My agents
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "directory"}
          className={tabClass(tab === "directory")}
          onClick={() => setTab("directory")}
        >
          Directory
        </button>
      </div>

      {tab === "mine" ? (
        <MyAgents />
      ) : (
        <AgentDirectory
          managementV2
          ownedIds={user && linked ? ownedIds : undefined}
          embedded
        />
      )}
    </div>
  );
}