import { useState } from "react";
import { AlertCircle, CheckCircle, Link as LinkIcon } from "lucide-react";
import { useAuth } from "../hooks/useAuth";
import api from "../services/api";

export default function Settings() {
  const { user, refresh } = useAuth();
  const [botName, setBotName] = useState("");
  const [linking, setLinking] = useState(false);
  const [success, setSuccess] = useState("");
  const [error, setError] = useState("");

  if (!user) return null;

  const handleLinkExchange = async () => {
    setError("");
    setSuccess("");
    setLinking(true);
    try {
      await api.post("/auth/link-exchange", {
        bot_name: botName || user.display_name,
        developer_id: user.display_name,
      });
      setSuccess("Exchange account linked successfully!");
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

      {/* Exchange Linking */}
      <div className="bg-white border border-gray-200 rounded-xl p-6">
        <h2 className="font-semibold text-navy-900 mb-4">
          A2A Settlement Exchange
        </h2>

        {error && (
          <div className="bg-red-50 text-red-700 rounded-lg p-3 mb-4 flex items-center gap-2 text-sm">
            <AlertCircle className="w-4 h-4" /> {error}
          </div>
        )}
        {success && (
          <div className="bg-green-50 text-green-700 rounded-lg p-3 mb-4 flex items-center gap-2 text-sm">
            <CheckCircle className="w-4 h-4" /> {success}
          </div>
        )}

        {user.exchange_bot_id ? (
          <div className="space-y-3 text-sm">
            <div className="flex items-center gap-2 text-money-dark">
              <CheckCircle className="w-5 h-5" />
              <span className="font-medium">Exchange account linked</span>
            </div>
            <div>
              <span className="text-gray-500">Bot ID:</span>{" "}
              <span className="font-mono text-xs">{user.exchange_bot_id}</span>
            </div>
            {user.exchange_balance_cached != null && (
              <div>
                <span className="text-gray-500">Balance:</span>{" "}
                <span className="font-semibold">
                  {user.exchange_balance_cached} ATE
                </span>
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-sm text-gray-600">
              Link your account to the A2A Settlement Exchange to post bounties,
              claim tasks, and receive payments.
            </p>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Bot Name (for the exchange)
              </label>
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
              className="flex items-center gap-2 px-5 py-2.5 bg-navy-900 text-white rounded-lg font-semibold text-sm hover:bg-navy-800 transition disabled:opacity-50"
            >
              <LinkIcon className="w-4 h-4" />
              {linking ? "Linking..." : "Link Exchange Account"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
