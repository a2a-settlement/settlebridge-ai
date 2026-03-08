import { ShieldCheck, ShieldAlert, Shield } from "lucide-react";
import type { ProvenanceTier } from "../types";

const config: Record<
  ProvenanceTier,
  { label: string; icon: typeof Shield; className: string }
> = {
  tier1_self_declared: {
    label: "Tier 1",
    icon: Shield,
    className: "bg-gray-100 text-gray-600",
  },
  tier2_signed: {
    label: "Tier 2",
    icon: ShieldAlert,
    className: "bg-blue-100 text-blue-700",
  },
  tier3_verifiable: {
    label: "Tier 3",
    icon: ShieldCheck,
    className: "bg-purple-100 text-purple-700",
  },
};

export default function ProvenanceBadge({ tier }: { tier: ProvenanceTier }) {
  const { label, icon: Icon, className } = config[tier];
  return (
    <span
      className={`inline-flex items-center gap-1 text-xs font-medium px-2.5 py-1 rounded-full ${className}`}
      title={`Provenance: ${tier.replace(/_/g, " ")}`}
    >
      <Icon className="w-3 h-3" />
      {label}
    </span>
  );
}
