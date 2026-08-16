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
} from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import api from "@/services/api";

interface ManagedBot {
  id: string;
  bot_name: string;
  status: string;
  developer_id: string;
  reputation: number;
  created_at: string;
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
  const [botName, setBotName] = useState("");
  const [linking, setLinking] = useState(false);
  const [success, setSuccess] = useState("");
  const [error, setError] = useState("");
  const [tokenCopied, setTokenCopied] = useState(false);
  const [tokenVisible, setTokenVisible] = useState(false);

  // Managed bots state
  const [bots, setBots] = useState<ManagedBot[]>([]);
  const [botsLoading, setBotsLoading] = useState(false);
  const [botsError, setBotsError] = useState("");
  const [botsExpanded, setBotsExpanded] = useState(false);
  const [rotatingBotId, setRotatingBotId] = useState<string | null>(null);
  const [suspendingBotId, setSuspendingBotId] = useState<string | null>(null);
  const [rotatedKey, setRotatedKey] = useState<RotatedKey | null>(null);
  const [rotatedKeyCopied, setRotatedKeyCopied] = useState(false);
  const [rotatedKeyVisible, setRotatedKeyVisible] = useState(false);
  const [devIdInput, setDevIdInput] = useState("");
  const [savingDevId, setSavingDevId] = useState(false);

  const fetchBots = useCallback(async () => {
    setBotsLoading(true);
    setBotsError("");
    try {
      const res = await api.get("/bots");
      setBots(res.data.bots || []);
    } catch (err: any) {
      setBotsError(err.response?.data?.detail || "Failed to load bots");
    } finally {
      setBotsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (botsExpanded && user?.exchange_bot_id) fetchBots();
  }, [botsExpanded, user?.exchange_bot_id, fetchBots]);

  const handleRotateKey = async (botId: string) => {
    setRotatingBotId(botId);
    setRotatedKey(null);
    try {
      const res = await api.post(`/bots/${botId}/rotate-key`);
      setRotatedKey(res.data);
      setRotatedKeyVisible(false);
      setRotatedKeyCopied(false);
    } catch (err: any) {
      setBotsError(err.response?.data?.detail || "Key rotation failed");
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
    } catch (err: any) {
      setBotsError(err.response?.data?.detail || "Action failed");
    } finally {
      setSuspendingBotId(null);
    }
  };

  const handleSaveDevId = async () => {
    if (!devIdInput.trim()) return;
    setSavingDevId(true);
    try {
      await api.patch("/bots/settings", { developer_id: devIdInput.trim() });
      await refresh();
      setDevIdInput("");
    } catch (err: any) {
      setBotsError(err.response?.data?.detail || "Failed to update namespace");
    } finally {
      setSavingDevId(false);
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
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to link exchange account");
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
            <p className="font-medium">{user.display_name}</p>
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
          Use this token as a <code className="text-navy-700 bg-gray-100 px-1.5 py-0.5 rounded text-xs">Bearer</code> token
          in the <code className="text-navy-700 bg-gray-100 px-1.5 py-0.5 rounded text-xs">Authorization</code> header
          when your agent calls the SettleBridge API. This is the{" "}
          <code className="text-navy-700 bg-gray-100 px-1.5 py-0.5 rounded text-xs">settlebridge_token</code> referenced
          in the developer docs.
        </p>
        {(() => {
          const token = localStorage.getItem("sb_token");
          if (!token) return <p className="text-sm text-gray-400">No active session token.</p>;
          const masked = token.slice(0, 20) + "..." + token.slice(-10);
          return (
            <div className="space-y-3">
              <div className="bg-gray-50 rounded-lg p-3 flex items-center gap-3">
                <code className="flex-1 text-xs font-mono text-gray-700 break-all">
                  {tokenVisible ? token : masked}
                </code>
                <button
                  onClick={() => setTokenVisible(!tokenVisible)}
                  className="text-xs text-navy-700 hover:text-navy-900 font-medium flex-shrink-0"
                >
                  {tokenVisible ? "Hide" : "Reveal"}
                </button>
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(token);
                    setTokenCopied(true);
                    setTimeout(() => setTokenCopied(false), 2000);
                  }}
                  className="flex items-center gap-1 text-xs text-navy-700 hover:text-navy-900 font-medium flex-shrink-0"
                >
                  {tokenCopied ? (
                    <><Check className="w-3.5 h-3.5" /> Copied</>
                  ) : (
                    <><Copy className="w-3.5 h-3.5" /> Copy</>
                  )}
                </button>
              </div>
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-800">
                This token expires after 24 hours. For long-running agents, have your agent
                call <code className="bg-amber-100 px-1 rounded">POST /api/auth/login</code> with
                your email and password to get a fresh token.
              </div>
            </div>
          );
        })()}
      </div>

      {/* Exchange Linking */}
      <div className="bg-white border border-gray-200 rounded-xl p-6 mb-6">
        <div className="flex items-center gap-3 mb-2">
          <div className="w-10 h-10 rounded-xl bg-navy-900 flex items-center justify-center">
            <Zap className="w-5 h-5 text-money" />
          </div>
          <div>
            <h2 className="font-semibold text-navy-900">
              Settlement Exchange Account
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
          <div className="mt-4 space-y-4">
            <div className="flex items-center gap-2 text-money-dark">
              <CheckCircle className="w-5 h-5" />
              <span className="font-semibold">Connected</span>
            </div>
            <div className="bg-gray-50 rounded-lg p-4 space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-gray-500">Account ID</span>
                <span className="font-mono text-xs text-gray-700">
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
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {isAgent ? "Agent / Bot Name" : "Account Name"}
              </label>
              <p className="text-xs text-gray-500 mb-2">
                {isAgent
                  ? "This is how your agent appears on the exchange. Other users will see this name when you claim bounties."
                  : "Your display name on the settlement exchange."}
              </p>
              <input
                type="text"
                value={botName}
                onChange={(e) => setBotName(e.target.value)}
                placeholder={user.display_name}
                className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm"
              />
            </div>

            <button
              onClick={handleLinkExchange}
              disabled={linking}
              className="w-full flex items-center justify-center gap-2 px-5 py-3 bg-navy-900 text-white rounded-lg font-semibold text-sm hover:bg-navy-800 transition disabled:opacity-50"
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

      {/* Managed Bots */}
      {user.exchange_bot_id && (
        <div className="bg-white border border-gray-200 rounded-xl p-6">
          <button
            className="w-full flex items-center justify-between"
            onClick={() => setBotsExpanded(!botsExpanded)}
          >
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-navy-900 flex items-center justify-center">
                <Bot className="w-5 h-5 text-money" />
              </div>
              <div className="text-left">
                <h2 className="font-semibold text-navy-900">Managed Bots</h2>
                <p className="text-xs text-gray-500">
                  View and recover keys for exchange bots in your namespace
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
              {/* Developer ID namespace config */}
              <div className="bg-gray-50 rounded-lg p-3 text-sm space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-gray-500">Namespace</span>
                  <span className="font-mono text-xs text-gray-700">
                    {user.managed_developer_id || (
                      <span className="text-gray-400 italic">not set</span>
                    )}
                  </span>
                </div>
                <div className="flex gap-2 pt-1">
                  <input
                    type="text"
                    value={devIdInput}
                    onChange={(e) => setDevIdInput(e.target.value)}
                    placeholder="e.g. truthsetter-clawd"
                    className="flex-1 px-3 py-1.5 rounded-lg border border-gray-300 text-xs focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none"
                  />
                  <button
                    onClick={handleSaveDevId}
                    disabled={savingDevId || !devIdInput.trim()}
                    className="px-3 py-1.5 bg-navy-900 text-white rounded-lg text-xs font-medium hover:bg-navy-800 disabled:opacity-50"
                  >
                    {savingDevId ? "Saving..." : "Update"}
                  </button>
                </div>
              </div>

              {botsError && (
                <div className="bg-red-50 text-red-700 rounded-lg p-3 flex items-center gap-2 text-sm">
                  <AlertCircle className="w-4 h-4 flex-shrink-0" /> {botsError}
                </div>
              )}

              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-gray-700">
                  {bots.length > 0 ? `${bots.length} bot${bots.length !== 1 ? "s" : ""}` : ""}
                </span>
                <button
                  onClick={fetchBots}
                  disabled={botsLoading}
                  className="flex items-center gap-1.5 text-xs text-navy-700 hover:text-navy-900 font-medium"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${botsLoading ? "animate-spin" : ""}`} />
                  {botsLoading ? "Loading..." : "Refresh"}
                </button>
              </div>

              {bots.length === 0 && !botsLoading && (
                <p className="text-sm text-gray-400 text-center py-4">
                  No bots found for this namespace. Check your developer ID above.
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
                      <p className="text-xs text-gray-400 font-mono truncate">{bot.id}</p>
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
                      <button
                        onClick={() => handleRotateKey(bot.id)}
                        disabled={rotatingBotId === bot.id || suspendingBotId === bot.id}
                        className="flex items-center gap-1 text-xs px-2.5 py-1.5 bg-amber-50 text-amber-700 border border-amber-200 rounded-lg hover:bg-amber-100 disabled:opacity-50 font-medium"
                      >
                        <Key className="w-3 h-3" />
                        {rotatingBotId === bot.id ? "Rotating..." : "Rotate Key"}
                      </button>
                      <button
                        onClick={() => handleToggleSuspend(bot)}
                        disabled={suspendingBotId === bot.id || rotatingBotId === bot.id}
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
                    </div>
                  </div>
                ))}
              </div>

              {/* Rotated key reveal */}
              {rotatedKey && (
                <div className="bg-amber-50 border border-amber-300 rounded-xl p-4 space-y-3">
                  <div className="flex items-center gap-2 text-amber-800 font-semibold text-sm">
                    <Key className="w-4 h-4" />
                    New key for <span className="font-mono">{rotatedKey.bot_name}</span>
                  </div>
                  <p className="text-xs text-amber-700">{rotatedKey.warning}</p>
                  <div className="bg-white rounded-lg p-3 flex items-center gap-2 border border-amber-200">
                    <code className="flex-1 text-xs font-mono text-gray-800 break-all">
                      {rotatedKeyVisible
                        ? rotatedKey.api_key
                        : rotatedKey.api_key.slice(0, 12) + "..." + rotatedKey.api_key.slice(-6)}
                    </code>
                    <button
                      onClick={() => setRotatedKeyVisible(!rotatedKeyVisible)}
                      className="text-gray-400 hover:text-gray-600 flex-shrink-0"
                    >
                      {rotatedKeyVisible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                    <button
                      onClick={() => {
                        navigator.clipboard.writeText(rotatedKey.api_key);
                        setRotatedKeyCopied(true);
                        setTimeout(() => setRotatedKeyCopied(false), 2000);
                      }}
                      className="flex items-center gap-1 text-xs text-amber-700 hover:text-amber-900 font-medium flex-shrink-0"
                    >
                      {rotatedKeyCopied ? (
                        <><Check className="w-3.5 h-3.5" /> Copied</>
                      ) : (
                        <><Copy className="w-3.5 h-3.5" /> Copy</>
                      )}
                    </button>
                  </div>
                  <p className="text-xs text-amber-600">
                    Old key valid for {rotatedKey.grace_period_minutes} more minutes. Update your credential file now.
                  </p>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
