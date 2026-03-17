import { Star } from "lucide-react";

interface Props {
  nativeReputation: number;
  rho: number;
  exchangeName: string;
  exchangeDid?: string;
}

export default function FederatedReputationBadge({
  nativeReputation,
  rho,
  exchangeName,
  exchangeDid,
}: Props) {
  const score = Math.round(nativeReputation * rho * 100);

  const color =
    score >= 80
      ? "text-money-dark"
      : score >= 60
        ? "text-yellow-600"
        : "text-red-500";

  return (
    <span
      className={`inline-flex items-center gap-1.5 font-semibold ${color}`}
      title={exchangeDid ? `Exchange: ${exchangeDid}` : undefined}
    >
      <Star className="w-4 h-4 shrink-0" />
      Reputation: {score}% (Federated via {exchangeName}, Trust Multiplier: {rho}×)
    </span>
  );
}
