"use client";

import { useCallback, useEffect, useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/hooks/useAuth";
import { useAppConfig } from "@/hooks/useAppConfig";
import { useManagedAgents } from "@/hooks/useManagedAgents";
import AgentDirectory from "@/components/marketplace-pages/AgentDirectory";
import MyAgents from "@/components/agents/MyAgents";
import GatewayAgentsTab from "@/components/agents/GatewayAgentsTab";

type AgentsTab = "mine" | "directory" | "gateway";

function AgentsAuthPlaceholder() {
  return (
    <div className="space-y-3" aria-busy="true" aria-live="polite">
      <div className="h-8 w-48 bg-gray-100 rounded-lg animate-pulse" />
      <div className="h-24 bg-gray-100 rounded-xl animate-pulse" />
      <div className="h-24 bg-gray-100 rounded-xl animate-pulse" />
    </div>
  );
}

function parseTab(
  raw: string | null,
  signedIn: boolean,
  allowGateway: boolean
): AgentsTab {
  if (raw === "directory") return "directory";
  if (raw === "mine") return "mine";
  if (raw === "gateway" && signedIn && allowGateway) return "gateway";
  return signedIn ? "mine" : "directory";
}

export default function AgentsPage() {
  const { agent_management_v2 } = useAppConfig();
  const { user, loading: authLoading } = useAuth();
  const searchParams = useSearchParams();
  const router = useRouter();

  const signedIn = !!user;
  const linked = !!user?.exchange_bot_id;
  const managed = useManagedAgents(agent_management_v2 && signedIn && linked);

  // Gateway tab is ONLY visible when signed in and one of the gateway conditions holds.
  const canShowGateway = signedIn && linked && managed.showGatewayTab;
  // Avoid prematurely bouncing ?tab=gateway while initial auth or /bots check is in flight.
  const checkingGatewayEligibility =
    authLoading || (signedIn && linked && managed.loading && !managed.data);

  const rawTab = searchParams.get("tab");

  const tab = useMemo(() => {
    // Don't treat a still-loading session as signed out (that flashes Directory
    // or the My agents sign-in empty state).
    if (authLoading && rawTab !== "directory" && rawTab !== "gateway") {
      return "mine" as AgentsTab;
    }
    return parseTab(
      rawTab,
      signedIn,
      canShowGateway || (rawTab === "gateway" && checkingGatewayEligibility)
    );
  }, [
    authLoading,
    rawTab,
    signedIn,
    canShowGateway,
    checkingGatewayEligibility,
  ]);

  const setTab = useCallback(
    (next: AgentsTab) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set("tab", next);
      router.replace(`/agents?${params.toString()}`, { scroll: false });
    },
    [router, searchParams]
  );

  useEffect(() => {
    if (!agent_management_v2 || authLoading) return;
    if (rawTab === "mine" || rawTab === "directory") return;
    if (rawTab === "gateway") {
      if (checkingGatewayEligibility || canShowGateway) return;
    }
    const defaultTab: AgentsTab = signedIn ? "mine" : "directory";
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", defaultTab);
    router.replace(`/agents?${params.toString()}`, { scroll: false });
  }, [
    agent_management_v2,
    authLoading,
    rawTab,
    checkingGatewayEligibility,
    canShowGateway,
    signedIn,
    searchParams,
    router,
  ]);

  if (!agent_management_v2) {
    return <AgentDirectory />;
  }

  const tabClass = (active: boolean) =>
    `px-4 py-2 rounded-lg text-sm font-medium transition focus:outline-none focus:ring-2 focus:ring-navy-500 ${
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
        className="inline-flex flex-wrap gap-1 p-1 bg-gray-100 rounded-xl mb-8"
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
        {canShowGateway && (
          <button
            type="button"
            role="tab"
            aria-selected={tab === "gateway"}
            className={tabClass(tab === "gateway")}
            onClick={() => setTab("gateway")}
          >
            Gateway
          </button>
        )}
      </div>

      {tab === "mine" ? (
        authLoading ? (
          <AgentsAuthPlaceholder />
        ) : (
          <MyAgents managed={managed} />
        )
      ) : tab === "gateway" && (canShowGateway || checkingGatewayEligibility) ? (
        <GatewayAgentsTab
          bots={managed.bots}
          gatewayClaimedAgents={managed.gatewayClaimedAgents}
          callerAccountType={managed.callerAccountType}
          callerGatewayId={user?.exchange_bot_id}
          callerDisplayName={user?.display_name}
          loading={checkingGatewayEligibility || managed.loading}
        />
      ) : (
        <AgentDirectory
          managementV2
          ownedIds={signedIn && linked ? managed.ownedIds : undefined}
          embedded
        />
      )}
    </div>
  );
}
