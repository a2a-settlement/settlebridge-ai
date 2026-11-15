"use client";

import { useState, useEffect, useCallback } from "react";
import {
  AlertCircle,
  CheckCircle,
  Link as LinkIcon,
  Zap,
  Shield,
  Coins,
  Bot,
  Key,
  Copy,
  Check,
  RefreshCw,
  Eye,
  EyeOff,
  ChevronDown,
  ChevronUp,
  Plus,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useAuth } from "@/hooks/useAuth";
import { useAppConfig } from "@/hooks/useAppConfig";
import { useManagedAgents } from "@/hooks/useManagedAgents";
import {
  extractApiError,
  type DetachDeveloperIdResponse,
} from "@/lib/managedAgents";
import api from "@/services/api";
import GitHubIdentity from "@/components/marketplace-pages/GitHubIdentity";
import AddDeveloperIdModal from "@/components/agents/AddDeveloperIdModal";

interface ManagedBot {
  id: string;
  bot_name: string;
  status: string;
  developer_id: string;
  reputation: number;
  created_at: string;
  own?: boolean;
}

interface RotatedKey {
  bot_id: string;
  bot_name: string;
  api_key: string;
  grace_period_minutes: number;
  warning: string;
}

export default function Settings() {
  const { user, refresh } = useAuth();
  const { agent_management_v2 } = useAppConfig();
  const [botName, setBotName] = useState("");
  const [linking, setLinking] = useState(false);
  const [success, setSuccess] = useState("");
  const [error, setError] = useState("");
  const [tokenCopied, setTokenCopied] = useState(false);
  const [tokenVisible, setTokenVisible] = useState(false);

  // V2 Developer ID attachment / removal state
  const v2Enabled = Boolean(agent_management_v2 && user?.exchange_bot_id);
  const {
    developerId: primaryDeveloperId,
    developerIds,
    loading: devIdsLoading,
    refresh: refreshManaged,
  } = useManagedAgents(v2Enabled);
  const [attachModalOpen, setAttachModalOpen] = useState(false);
  const [pendingDetachDevId, setPendingDetachDevId] = useState<string | null>(
    null
  );
  const [detaching, setDetaching] = useState(false);
  const [devIdError, setDevIdError] = useState("");
  const [devIdNotice, setDevIdNotice] = useState("");

  // Legacy Managed bots state (when agent_management_v2 is false)
  const [bots, setBots] = useState<ManagedBot[]>([]);
  const [botsLoading, setBotsLoading] = useState(false);
  const [botsError, setBotsError] = useState("");
  const [botsExpanded, setBotsExpanded] = useState(false);
  const [rotatingBotId, setRotatingBotId] = useState<string | null>(null);
  const [suspendingBotId, setSuspendingBotId] = useState<string | null>(null);
  const [rotatedKey, setRotatedKey] = useState<RotatedKey | null>(null);
  const [rotatedKeyCopied, setRotatedKeyCopied] = useState(false);
  const [rotatedKeyVisible, setRotatedKeyVisible] = useState(false);
  const [legacyDevId, setLegacyDevId] = useState<string | null>(null);

  const fetchBots = useCallback(async () => {
    setBotsLoading(true);
    setBotsError("");
    try {
      const res = await api.get("/bots");
      setBots(res.data.bots || []);
      setLegacyDevId(res.data.developer_id || null);
    } catch (err: unknown) {
      setBotsError(extractApiError(err, "Failed to load agents"));
    } finally {
      setBotsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!agent_management_v2 && botsExpanded && user?.exchange_bot_id) {
      void fetchBots();
    }
  }, [agent_management_v2, botsExpanded, user?.exchange_bot_id, fetchBots]);

  const handleRotateKey = async (botId: string) => {
    setRotatingBotId(botId);
    setRotatedKey(null);
    try {
      const res = await api.post(`/bots/${botId}/rotate-key`);
      setRotatedKey(res.data);
      setRotatedKeyVisible(false);
      setRotatedKeyCopied(false);
    } catch (err: unknown) {
      setBotsError(extractApiError(err, "Key rotation failed"));
    } finally {
      setRotatingBotId(null);
    }
  };

  const handleToggleSuspend = async (bot: ManagedBot) => {
    setSuspendingBotId(bot.id);
    setBotsError("");
    try {
      const action = bot.status === "active" ? "suspend" : "unsuspend";
      await api.post(`/bots/${bot.id}/${action}`);
      await fetchBots();
    } catch (err: unknown) {
      setBotsError(extractApiError(err, "Action failed"));
    } finally {
      setSuspendingBotId(null);
    }
  };

  const confirmDetachDeveloperId = async () => {
    if (!pendingDetachDevId) return;
    setDetaching(true);
    setDevIdError("");
    setDevIdNotice("");
    try {
      await api.delete<DetachDeveloperIdResponse>(
        `/bots/developer-ids/${encodeURIComponent(pendingDetachDevId)}`
      );
      const removed = pendingDetachDevId;
      setPendingDetachDevId(null);
      setDevIdNotice(`Removed Developer ID "${removed}".`);
      await refreshManaged();
    } catch (err: unknown) {
      setDevIdError(extractApiError(err, "Failed to remove Developer ID"));
    } finally {
      setDetaching(false);
    }
  };

  if (!user) return null;

  const isAgent =
    user.user_type === "agent_operator" || user.user_type === "both";
  const isRequester =
    user.user_type === "requester" || user.user_type === "both";

  const handleLinkExchange = async () => {
    setError("");
    setSuccess("");
    setLinking(true);
    try {
      await api.post("/auth/link-exchange", {
        bot_name: botName || user.display_name,
        developer_id: user.display_name,
      });
      setSuccess(
        "Exchange account created and linked! You're ready to go."
      );
      await refresh();
    } catch (err: unknown) {
      setError(extractApiError(err, "Failed to link exchange account"));
    } finally {
      setLinking(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <h1 className="text-2xl font-bold text-navy-900 mb-8">Settings</h1>

      {/* Account Info */}
      <div className="bg-white border border-gray-200 rounded-xl p-6 mb-6">
        <h2 className="font-semibold text-navy-900 mb-4">Account</h2>
        <div className="grid sm:grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-gray-500">Email</span>
            <p className="font-medium">{user.email}</p>
          </div>
          <div>
            <span className="text-gray-500">Display Name</span>
            <p className="font-medium flex items-center gap-2 flex-wrap">
              {user.display_name}
              {user.account_kind === "test" && (
                <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-amber-50 text-amber-800 border border-amber-200">
                  Test account
                </span>
              )}
              {user.account_kind === "house" && (
                <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-navy-100 text-navy-800 border border-navy-200">
                  House agent
                </span>
              )}
            </p>
          </div>
          <div>
            <span className="text-gray-500">Role</span>
            <p className="font-medium capitalize">
              {user.user_type.replace("_", " ")}
            </p>
          </div>
          <div>
            <span className="text-gray-500">Member Since</span>
            <p className="font-medium">
              {new Date(user.created_at).toLocaleDateString()}
            </p>
          </div>
        </div>
      </div>

      {/* API Token */}
      <div className="bg-white border border-gray-200 rounded-xl p-6 mb-6">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-xl bg-navy-900 flex items-center justify-center">
            <Key className="w-5 h-5 text-money" />
          </div>
          <div>
            <h2 className="font-semibold text-navy-900">API Token</h2>
            <p className="text-xs text-gray-500">
              For programmatic access to the SettleBridge API
            </p>
          </div>
        </div>
        <p className="text-sm text-gray-600 mb-3">
          Use this token as a{" "}
          <code className="text-navy-700 bg-gray-100 px-1.5 py-0.5 rounded text-xs">
            Bearer
          </code>{" "}
          token in the{" "}
          <code className="text-navy-700 bg-gray-100 px-1.5 py-0.5 rounded text-xs">
            Authorization
          </code>{" "}
          header when your agent calls the SettleBridge API. This is the{" "}
          <code className="text-navy-700 bg-gray-100 px-1.5 py-0.5 rounded text-xs">
            settlebridge_token
          </code>{" "}
          referenced in the developer docs.
        </p>
        {(() => {
          const token = localStorage.getItem("sb_token");
          if (!token)
            return (
              <p className="text-sm text-gray-400">No active session token.</p>
            );
          const masked = token.slice(0, 20) + "..." + token.slice(-10);
          return (
            <div className="space-y-3">
              <div className="bg-gray-50 rounded-lg p-3 flex items-center gap-3">
                <code className="flex-1 text-xs font-mono text-gray-700 break-all">
                  {tokenVisible ? token : masked}
                </code>
                <button
                  type="button"
                  onClick={() => setTokenVisible(!tokenVisible)}
                  className="text-xs text-navy-700 hover:text-navy-900 font-medium flex-shrink-0 rounded px-1 focus:outline-none focus:ring-2 focus:ring-navy-500"
                >
                  {tokenVisible ? "Hide" : "Reveal"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    void navigator.clipboard.writeText(token);
                    setTokenCopied(true);
                    setTimeout(() => setTokenCopied(false), 2000);
                  }}
                  className="flex items-center gap-1 text-xs text-navy-700 hover:text-navy-900 font-medium flex-shrink-0 rounded px-1 focus:outline-none focus:ring-2 focus:ring-navy-500"
                >
                  {tokenCopied ? (
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
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-800">
                This token expires after 24 hours. For long-running agents, have
                your agent call{" "}
                <code className="bg-amber-100 px-1 rounded">
                  POST /api/auth/login
                </code>{" "}
                with your email and password to get a fresh token.
              </div>
            </div>
          );
        })()}
      </div>

      {/* Exchange Linking / Your account */}
      <div className="bg-white border border-gray-200 rounded-xl p-6 mb-6">
        <div className="flex items-center gap-3 mb-2">
          <div className="w-10 h-10 rounded-xl bg-navy-900 flex items-center justify-center">
            <Zap className="w-5 h-5 text-money" />
          </div>
          <div>
            <h2 className="font-semibold text-navy-900">
              {agent_management_v2
                ? "Your account"
                : "Settlement Exchange Account"}
            </h2>
            <p className="text-xs text-gray-500">
              Powered by A2A Settlement Protocol
            </p>
          </div>
        </div>

        {error && (
          <div className="bg-red-50 text-red-700 rounded-lg p-3 mb-4 flex items-center gap-2 text-sm mt-4">
            <AlertCircle className="w-4 h-4 flex-shrink-0" /> {error}
          </div>
        )}
        {success && (
          <div className="bg-green-50 text-green-700 rounded-lg p-3 mb-4 flex items-center gap-2 text-sm mt-4">
            <CheckCircle className="w-4 h-4 flex-shrink-0" /> {success}
          </div>
        )}

        {user.exchange_bot_id ? (
          <div className="mt-4 space-y-5">
            <div className="flex items-center gap-2 text-money-dark">
              <CheckCircle className="w-5 h-5" />
              <span className="font-semibold">Connected</span>
            </div>
            <div className="bg-gray-50 rounded-lg p-4 space-y-2 text-sm">
              <div className="flex justify-between gap-2">
                <span className="text-gray-500">Account ID</span>
                <span className="font-mono text-xs text-gray-700 break-all text-right">
                  {user.exchange_bot_id}
                </span>
              </div>
              {user.exchange_balance_cached != null && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Balance</span>
                  <span className="font-bold text-money-dark">
                    {user.exchange_balance_cached} ATE
                  </span>
                </div>
              )}
            </div>

            {agent_management_v2 && (
              <div className="border-t border-gray-200 pt-5">
                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-3">
                  <div>
                    <h3 className="text-sm font-semibold text-navy-900">
                      Attached Developer IDs
                    </h3>
                    <p className="text-xs text-gray-500 mt-0.5">
                      Developer IDs you control on the settlement exchange
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setDevIdError("");
                      setDevIdNotice("");
                      setAttachModalOpen(true);
                    }}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-gray-300 rounded-lg text-xs font-semibold text-navy-900 hover:bg-gray-50 transition self-start sm:self-auto focus:outline-none focus:ring-2 focus:ring-navy-500"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    Add developer ID
                  </button>
                </div>

                {devIdError && (
                  <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg p-3 mb-3 flex items-center gap-2 text-xs">
                    <AlertCircle className="w-4 h-4 flex-shrink-0" />
                    <span>{devIdError}</span>
                  </div>
                )}
                {devIdNotice && (
                  <div className="bg-green-50 border border-green-200 text-green-800 rounded-lg p-3 mb-3 flex items-center gap-2 text-xs">
                    <CheckCircle className="w-4 h-4 flex-shrink-0" />
                    <span>{devIdNotice}</span>
                  </div>
                )}

                {devIdsLoading && developerIds.length === 0 ? (
                  <div className="h-12 bg-gray-100 rounded-lg animate-pulse" />
                ) : developerIds.length === 0 ? (
                  <p className="text-xs text-gray-500">
                    No Developer IDs loaded yet.
                  </p>
                ) : (
                  <div className="divide-y divide-gray-100 border border-gray-200 rounded-xl">
                    {developerIds.map((devId) => {
                      const isPrimary = devId === primaryDeveloperId;
                      return (
                        <div
                          key={devId}
                          className="px-4 py-3 flex items-center justify-between gap-3"
                        >
                          <div className="flex items-center gap-2 min-w-0 flex-wrap">
                            <span className="font-mono text-xs sm:text-sm text-navy-900 break-all">
                              {devId}
                            </span>
                            {isPrimary && (
                              <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-navy-100 text-navy-800 border border-navy-200">
                                Primary
                              </span>
                            )}
                          </div>
                          <button
                            type="button"
                            disabled={isPrimary || detaching}
                            title={
                              isPrimary
                                ? "Your primary Developer ID cannot be removed"
                                : `Remove Developer ID ${devId}`
                            }
                            onClick={() => {
                              setDevIdError("");
                              setDevIdNotice("");
                              setPendingDetachDevId(devId);
                            }}
                            className="inline-flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-lg border border-gray-200 text-red-700 hover:bg-red-50 hover:border-red-200 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:border-gray-200 font-medium flex-shrink-0 focus:outline-none focus:ring-2 focus:ring-navy-500"
                          >
                            <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />
                            Remove
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="mt-4 space-y-5">
            <p className="text-sm text-gray-600 leading-relaxed">
              SettleBridge uses a settlement exchange to handle payments between
              bounty posters and agents. Linking creates your exchange identity
              so you can transact with ATE tokens.
            </p>

            <div className="grid sm:grid-cols-3 gap-3">
              {isRequester && (
                <div className="bg-gray-50 rounded-lg p-3">
                  <Coins className="w-4 h-4 text-navy-700 mb-1.5" />
                  <p className="text-xs font-medium text-navy-900">
                    Fund Bounties
                  </p>
                  <p className="text-xs text-gray-500 mt-0.5">
                    Escrow ATE tokens when publishing tasks
                  </p>
                </div>
              )}
              {isAgent && (
                <div className="bg-gray-50 rounded-lg p-3">
                  <Bot className="w-4 h-4 text-navy-700 mb-1.5" />
                  <p className="text-xs font-medium text-navy-900">
                    Register Agent
                  </p>
                  <p className="text-xs text-gray-500 mt-0.5">
                    Your agent needs an exchange identity to claim work
                  </p>
                </div>
              )}
              <div className="bg-gray-50 rounded-lg p-3">
                <Shield className="w-4 h-4 text-navy-700 mb-1.5" />
                <p className="text-xs font-medium text-navy-900">
                  Secure Settlement
                </p>
                <p className="text-xs text-gray-500 mt-0.5">
                  Escrow-backed payments with provenance tracking
                </p>
              </div>
            </div>

            <div className="border-t pt-5">
              <label
                htmlFor="settings-exchange-agent-name"
                className="block text-sm font-medium text-gray-700 mb-1"
              >
                {isAgent ? "Agent Name" : "Account Name"}
              </label>
              <p className="text-xs text-gray-500 mb-2">
                {isAgent
                  ? "This is how your agent appears on the exchange. Other users will see this name when you claim bounties."
                  : "Your display name on the settlement exchange."}
              </p>
              <input
                id="settings-exchange-agent-name"
                type="text"
                value={botName}
                onChange={(e) => setBotName(e.target.value)}
                placeholder={user.display_name}
                className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm"
              />
            </div>

            <button
              type="button"
              onClick={handleLinkExchange}
              disabled={linking}
              className="w-full flex items-center justify-center gap-2 px-5 py-3 bg-navy-900 text-white rounded-lg font-semibold text-sm hover:bg-navy-800 transition disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-navy-500"
            >
              <LinkIcon className="w-4 h-4" />
              {linking
                ? "Creating exchange account..."
                : "Create & Link Exchange Account"}
            </button>

            <p className="text-xs text-gray-400 text-center">
              This creates a new account on the A2A Settlement Exchange and
              links it to your SettleBridge profile. One-time setup.
            </p>
          </div>
        )}
      </div>

      <GitHubIdentity />

      {/* Agent management (v2) or legacy Managed Agents */}
      {user.exchange_bot_id && agent_management_v2 && (
        <div className="bg-white border border-gray-200 rounded-xl p-6">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-navy-900 flex items-center justify-center">
              <Bot className="w-5 h-5 text-money" />
            </div>
            <div className="flex-1">
              <h2 className="font-semibold text-navy-900">Agents</h2>
              <p className="text-sm text-gray-500">
                Suspend, rotate keys, publish Agent Cards, and register agents from one place.
              </p>
            </div>
          </div>
          <Link
            href="/agents?tab=mine"
            className="mt-4 inline-flex px-4 py-2.5 bg-navy-900 text-white rounded-lg text-sm font-semibold hover:bg-navy-800 transition focus:outline-none focus:ring-2 focus:ring-navy-500"
          >
            Manage your agents
          </Link>
        </div>
      )}

      {user.exchange_bot_id && !agent_management_v2 && (
        <div className="bg-white border border-gray-200 rounded-xl p-6">
          <button
            type="button"
            className="w-full flex items-center justify-between"
            onClick={() => setBotsExpanded(!botsExpanded)}
          >
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-navy-900 flex items-center justify-center">
                <Bot className="w-5 h-5 text-money" />
              </div>
              <div className="text-left">
                <h2 className="font-semibold text-navy-900">Managed Agents</h2>
                <p className="text-xs text-gray-500">
                  View and recover keys for exchange agents under your Developer ID
                </p>
              </div>
            </div>
            {botsExpanded ? (
              <ChevronUp className="w-5 h-5 text-gray-400" />
            ) : (
              <ChevronDown className="w-5 h-5 text-gray-400" />
            )}
          </button>

          {botsExpanded && (
            <div className="mt-5 space-y-4">
              <div className="bg-gray-50 rounded-lg p-3 text-sm">
                <div className="flex items-center justify-between">
                  <span className="text-gray-500">Developer ID</span>
                  <span className="font-mono text-xs text-gray-700">
                    {legacyDevId || (
                      <span className="text-gray-400 italic">unknown</span>
                    )}
                  </span>
                </div>
                <p className="text-xs text-gray-400 mt-1">
                  The Developer ID of your linked exchange account. Only agents
                  registered under it are listed.
                </p>
              </div>

              {botsError && (
                <div className="bg-red-50 text-red-700 rounded-lg p-3 flex items-center gap-2 text-sm">
                  <AlertCircle className="w-4 h-4 flex-shrink-0" /> {botsError}
                </div>
              )}

              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-gray-700">
                  {bots.length > 0
                    ? `${bots.length} agent${bots.length !== 1 ? "s" : ""}`
                    : ""}
                </span>
                <button
                  type="button"
                  onClick={fetchBots}
                  disabled={botsLoading}
                  className="flex items-center gap-1.5 text-xs text-navy-700 hover:text-navy-900 font-medium"
                >
                  <RefreshCw
                    className={`w-3.5 h-3.5 ${botsLoading ? "animate-spin" : ""}`}
                  />
                  {botsLoading ? "Loading..." : "Refresh"}
                </button>
              </div>

              {bots.length === 0 && !botsLoading && (
                <p className="text-sm text-gray-400 text-center py-4">
                  No agents found under this Developer ID.
                </p>
              )}

              <div className="space-y-2">
                {bots.map((bot) => (
                  <div
                    key={bot.id}
                    className="border border-gray-200 rounded-lg p-3 flex items-center justify-between gap-3"
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-navy-900 truncate">
                        {bot.bot_name}
                      </p>
                      <p className="text-xs text-gray-400 font-mono truncate">
                        {bot.id}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <span
                        className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                          bot.status === "active"
                            ? "bg-green-100 text-green-700"
                            : "bg-gray-100 text-gray-500"
                        }`}
                      >
                        {bot.status}
                      </span>
                      {bot.own && (
                        <button
                          type="button"
                          onClick={() => handleRotateKey(bot.id)}
                          disabled={
                            rotatingBotId === bot.id ||
                            suspendingBotId === bot.id
                          }
                          className="flex items-center gap-1 text-xs px-2.5 py-1.5 bg-amber-50 text-amber-700 border border-amber-200 rounded-lg hover:bg-amber-100 disabled:opacity-50 font-medium"
                        >
                          <Key className="w-3 h-3" />
                          {rotatingBotId === bot.id
                            ? "Rotating..."
                            : "Rotate Key"}
                        </button>
                      )}
                      {user?.is_admin &&
                        !(bot.own && bot.status === "active") && (
                          <button
                            type="button"
                            onClick={() => handleToggleSuspend(bot)}
                            disabled={
                              suspendingBotId === bot.id ||
                              rotatingBotId === bot.id
                            }
                            className={`flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-lg border font-medium disabled:opacity-50 ${
                              bot.status === "active"
                                ? "bg-red-50 text-red-600 border-red-200 hover:bg-red-100"
                                : "bg-green-50 text-green-700 border-green-200 hover:bg-green-100"
                            }`}
                          >
                            {suspendingBotId === bot.id
                              ? "..."
                              : bot.status === "active"
                                ? "Suspend"
                                : "Reactivate"}
                          </button>
                        )}
                    </div>
                  </div>
                ))}
              </div>

              {rotatedKey && (
                <div className="bg-amber-50 border border-amber-300 rounded-xl p-4 space-y-3">
                  <div className="flex items-center gap-2 text-amber-800 font-semibold text-sm">
                    <Key className="w-4 h-4" />
                    New key for{" "}
                    <span className="font-mono">{rotatedKey.bot_name}</span>
                  </div>
                  <p className="text-xs text-amber-700">{rotatedKey.warning}</p>
                  <div className="bg-white rounded-lg p-3 flex items-center gap-2 border border-amber-200">
                    <code className="flex-1 text-xs font-mono text-gray-800 break-all">
                      {rotatedKeyVisible
                        ? rotatedKey.api_key
                        : rotatedKey.api_key.slice(0, 12) +
                          "..." +
                          rotatedKey.api_key.slice(-6)}
                    </code>
                    <button
                      type="button"
                      onClick={() => setRotatedKeyVisible(!rotatedKeyVisible)}
                      className="text-gray-400 hover:text-gray-600 flex-shrink-0"
                    >
                      {rotatedKeyVisible ? (
                        <EyeOff className="w-4 h-4" />
                      ) : (
                        <Eye className="w-4 h-4" />
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        void navigator.clipboard.writeText(rotatedKey.api_key);
                        setRotatedKeyCopied(true);
                        setTimeout(() => setRotatedKeyCopied(false), 2000);
                      }}
                      className="flex items-center gap-1 text-xs text-amber-700 hover:text-amber-900 font-medium flex-shrink-0"
                    >
                      {rotatedKeyCopied ? (
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
                  <p className="text-xs text-amber-600">
                    Old key valid for {rotatedKey.grace_period_minutes} more
                    minutes. Update your credential file now.
                  </p>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {attachModalOpen && (
        <AddDeveloperIdModal
          onClose={() => setAttachModalOpen(false)}
          onAttached={(attachedId) => {
            setAttachModalOpen(false);
            setDevIdNotice(`Attached Developer ID "${attachedId}".`);
            void refreshManaged();
          }}
        />
      )}

      {pendingDetachDevId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40">
          <div
            className="bg-white border border-gray-200 rounded-xl max-w-md w-full p-6 shadow-xl"
            role="dialog"
            aria-modal="true"
            aria-labelledby="detach-dev-id-title"
          >
            <h3
              id="detach-dev-id-title"
              className="font-semibold text-navy-900 mb-2"
            >
              Remove Developer ID
            </h3>
            <p className="text-sm text-gray-600 mb-6">
              Remove{" "}
              <span className="font-mono font-medium text-navy-900">
                {pendingDetachDevId}
              </span>{" "}
              from your account? Agents registered under this Developer ID stay
              active on the exchange, and you can re-attach it later using an
              agent API key.
            </p>
            <div className="flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setPendingDetachDevId(null)}
                disabled={detaching}
                className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900 rounded-lg focus:outline-none focus:ring-2 focus:ring-navy-500"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void confirmDetachDeveloperId()}
                disabled={detaching}
                className="px-4 py-2 bg-red-600 text-white rounded-lg text-sm font-semibold hover:bg-red-700 transition disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-navy-500"
              >
                {detaching ? "Removing…" : "Remove"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
