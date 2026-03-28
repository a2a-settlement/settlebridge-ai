import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Shield, Key, Mail } from "lucide-react";
import { useAuth } from "../hooks/useAuth";

type Tab = "email" | "apikey";

export default function Login() {
  const { login, exchangeLogin } = useAuth();
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("email");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleEmailSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(email, password);
      navigate("/dashboard");
    } catch (err: any) {
      setError(err.response?.data?.detail || "Login failed");
    } finally {
      setLoading(false);
    }
  };

  const handleApiKeySubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await exchangeLogin(apiKey);
      navigate("/dashboard");
    } catch (err: any) {
      setError(err.response?.data?.detail || "Login failed");
    } finally {
      setLoading(false);
    }
  };

  const tabClass = (t: Tab) =>
    `flex-1 flex items-center justify-center gap-2 py-2.5 text-sm font-medium rounded-lg transition ${
      tab === t
        ? "bg-navy-900 text-white shadow-sm"
        : "text-gray-500 hover:text-gray-700"
    }`;

  return (
    <div className="min-h-screen bg-navy-900 flex items-center justify-center px-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <Link to="/" className="inline-flex items-center gap-2 text-white">
            <Shield className="w-8 h-8 text-money" />
            <span className="text-xl font-bold">SettleBridge.ai</span>
          </Link>
        </div>
        <div className="bg-white rounded-2xl shadow-xl p-8">
          <h1 className="text-2xl font-bold text-navy-900 mb-6">Sign in</h1>

          <div className="flex bg-gray-100 rounded-lg p-1 mb-6">
            <button type="button" className={tabClass("email")} onClick={() => { setTab("email"); setError(""); }}>
              <Mail className="w-4 h-4" /> Email
            </button>
            <button type="button" className={tabClass("apikey")} onClick={() => { setTab("apikey"); setError(""); }}>
              <Key className="w-4 h-4" /> API Key
            </button>
          </div>

          {error && (
            <div className="bg-red-50 text-red-700 text-sm rounded-lg p-3 mb-4">
              {error}
            </div>
          )}

          {tab === "email" ? (
            <form onSubmit={handleEmailSubmit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Email
                </label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm"
                  placeholder="you@example.com"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Password
                </label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm"
                />
              </div>
              <button
                type="submit"
                disabled={loading}
                className="w-full py-2.5 bg-navy-900 text-white rounded-lg font-semibold text-sm hover:bg-navy-800 transition disabled:opacity-50"
              >
                {loading ? "Signing in..." : "Sign in"}
              </button>
            </form>
          ) : (
            <form onSubmit={handleApiKeySubmit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Exchange API Key
                </label>
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  required
                  className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm font-mono"
                  placeholder="ate_..."
                />
                <p className="text-xs text-gray-400 mt-1">
                  Your A2A Settlement Exchange API key
                </p>
              </div>
              <button
                type="submit"
                disabled={loading}
                className="w-full py-2.5 bg-navy-900 text-white rounded-lg font-semibold text-sm hover:bg-navy-800 transition disabled:opacity-50"
              >
                {loading ? "Signing in..." : "Sign in with API Key"}
              </button>
            </form>
          )}

          <p className="text-sm text-gray-500 text-center mt-6">
            Don't have an account?{" "}
            <Link
              to="/register"
              className="text-navy-700 font-medium hover:underline"
            >
              Create one
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
