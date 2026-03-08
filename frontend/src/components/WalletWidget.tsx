import { Wallet } from "lucide-react";
import { useAuth } from "../hooks/useAuth";

export default function WalletWidget() {
  const { user } = useAuth();

  if (!user?.exchange_bot_id) return null;

  const balance = user.exchange_balance_cached;

  return (
    <div className="hidden sm:flex items-center gap-1.5 text-sm text-gray-300" title="ATE Token Balance">
      <Wallet className="w-4 h-4" />
      <span className="font-medium">
        {balance != null ? `${balance} ATE` : "—"}
      </span>
    </div>
  );
}
