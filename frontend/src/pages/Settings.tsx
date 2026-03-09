import { useState } from "react";
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
} from "lucide-react";
import { useAuth } from "../hooks/useAuth";
import api from "../services/api";

export default function Settings() {
  const { user, refresh } = useAuth();
  const [botName, setBotName] = useState("");
  const [linking, setLinking] = useState(false);
  const [success, setSuccess] = useState("");
  const [error, setError] = useState("");
  const [tokenCopied, setTokenCopied] = useState(false);
  const [tokenVisible, setTokenVisible] = useState(false);

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
      <div className="bg-white border border-gray-200 rounded-xl p-6">
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
    </div>
  );
}
