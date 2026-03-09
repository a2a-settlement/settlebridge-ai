import { Wallet } from "lucide-react";
import { Link } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";

export default function WalletWidget() {
  const { user } = useAuth();

  if (!user?.exchange_bot_id) {
    return (
      <Link
        to="/dashboard/settings"
        className="hidden sm:flex items-center gap-1.5 text-xs text-amber-400 hover:text-amber-300 transition"
        title="Link your exchange account"
      >
        <Wallet className="w-4 h-4" />
        <span className="font-medium">Link Wallet</span>
      </Link>
    );
  }

  const balance = user.exchange_balance_cached;

  return (
    <Link
      to="/dashboard/settings"
      className="hidden sm:flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-navy-800 hover:bg-navy-700 transition"
      title="ATE Token Balance"
    >
      <Wallet className="w-4 h-4 text-money" />
      <span className="text-sm font-bold text-money">
        {balance != null ? balance.toLocaleString() : "—"}
      </span>
      <span className="text-xs text-gray-400">ATE</span>
    </Link>
  );
}
