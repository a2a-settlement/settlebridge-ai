import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Shield } from "lucide-react";
import { useAuth } from "../hooks/useAuth";

export default function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [userType, setUserType] = useState("requester");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await register(email, password, displayName, userType);
      navigate("/dashboard");
    } catch (err: any) {
      setError(err.response?.data?.detail || "Registration failed");
    } finally {
      setLoading(false);
    }
  };

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
          <h1 className="text-2xl font-bold text-navy-900 mb-6">
            Create account
          </h1>
          {error && (
            <div className="bg-red-50 text-red-700 text-sm rounded-lg p-3 mb-4">
              {error}
            </div>
          )}
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Display Name
              </label>
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                required
                className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm"
              />
            </div>
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
                minLength={8}
                className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                I am a...
              </label>
              <div className="grid grid-cols-3 gap-2">
                {[
                  { value: "requester", label: "Requester" },
                  { value: "agent_operator", label: "Agent Operator" },
                  { value: "both", label: "Both" },
                ].map(({ value, label }) => (
                  <button
                    type="button"
                    key={value}
                    onClick={() => setUserType(value)}
                    className={`px-3 py-2 rounded-lg text-sm font-medium border transition ${
                      userType === value
                        ? "bg-navy-900 text-white border-navy-900"
                        : "bg-white text-gray-600 border-gray-300 hover:border-navy-400"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 bg-navy-900 text-white rounded-lg font-semibold text-sm hover:bg-navy-800 transition disabled:opacity-50"
            >
              {loading ? "Creating..." : "Create Account"}
            </button>
          </form>
          <p className="text-sm text-gray-500 text-center mt-6">
            Already have an account?{" "}
            <Link to="/login" className="text-navy-700 font-medium hover:underline">
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
