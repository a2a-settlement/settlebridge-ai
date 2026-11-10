import { useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";

export default function AdminOnly() {
  const { logout } = useAuth();
  const navigate = useNavigate();

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-bold text-gray-900">
        Gateway console is for platform admins
      </h1>
      <p className="text-sm text-gray-500 mt-1">
        Your account doesn't have access to the gateway console. Bounties, repositories and your bots are in the marketplace.
      </p>
      <div className="mt-6 flex items-center gap-4">
        <a
          href="https://market.settlebridge.ai/dashboard"
          className="px-4 py-2 bg-money text-navy-900 rounded-lg text-sm font-semibold hover:bg-money-dark transition"
        >
          Marketplace dashboard
        </a>
        <button
          type="button"
          onClick={() => {
            logout();
            navigate("/login");
          }}
          className="text-sm text-gray-500 hover:text-gray-900 transition"
        >
          Sign out
        </button>
      </div>
    </div>
  );
}
