import { ShieldCheck, ShieldAlert, ShieldX, ShieldQuestion } from "lucide-react";
import type { AttestationFreshness } from "../types";

interface Props {
  freshness: AttestationFreshness | null | undefined;
  compact?: boolean;
}

const statusConfig: Record<
  string,
  { icon: typeof ShieldCheck; color: string; label: string }
> = {
  active: { icon: ShieldCheck, color: "text-money-dark", label: "Verified" },
  expired: { icon: ShieldAlert, color: "text-yellow-600", label: "Expired" },
  revoked: { icon: ShieldX, color: "text-red-500", label: "Revoked" },
  unknown: {
    icon: ShieldQuestion,
    color: "text-gray-400",
    label: "Unverified",
  },
};

function daysAgoLabel(days: number | null): string {
  if (days === null) return "never";
  if (days === 0) return "today";
  if (days === 1) return "1 day ago";
  return `${days} days ago`;
}

export default function AttestationFreshnessBadge({
  freshness,
  compact = false,
}: Props) {
  if (!freshness) {
    return (
      <span className="inline-flex items-center gap-1 text-gray-400 text-xs">
        <ShieldQuestion className="w-3.5 h-3.5" />
        {!compact && "No attestation data"}
      </span>
    );
  }

  const cfg = statusConfig[freshness.identity_status] ?? statusConfig.unknown;
  const Icon = cfg.icon;

  if (compact) {
    return (
      <span
        className={`inline-flex items-center gap-1 text-xs font-medium ${cfg.color}`}
        title={`Identity ${cfg.label} ${daysAgoLabel(freshness.identity_verified_days_ago)}`}
      >
        <Icon className="w-3.5 h-3.5" />
        {cfg.label}
      </span>
    );
  }

  return (
    <div className={`inline-flex items-center gap-1.5 text-sm ${cfg.color}`}>
      <Icon className="w-4 h-4" />
      <span className="font-medium">{cfg.label}</span>
      <span className="text-gray-500 font-normal">
        &middot; Identity verified{" "}
        {daysAgoLabel(freshness.identity_verified_days_ago)}
      </span>
    </div>
  );
}
