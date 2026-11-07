import type { CyberFundingState, RepoStatus, StatusBadgeProps } from "@/lib/cyberContracts";
import { fundingStatus, repoStatus, stepStatus, type StatusVisual } from "@/lib/statusVocabulary";

function detailText(reason: string | undefined): string | null {
  if (!reason) return null;
  const trimmed = reason.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function StatusGlyph({
  visual,
  label,
  reason,
  size = "md",
  dashed = false,
}: {
  visual: StatusVisual;
  label: string;
  reason?: string;
  size?: "sm" | "md";
  dashed?: boolean;
}) {
  const Icon = visual.icon;
  const detail = detailText(reason);
  const iconClass = size === "sm" ? "h-3.5 w-3.5" : "h-4 w-4";
  const textClass = size === "sm" ? "gap-1 text-xs" : "gap-1.5 text-sm";

  return (
    <span
      className={`inline-flex flex-wrap items-center font-medium ${textClass} ${visual.textClass}${
        dashed ? " rounded-md border border-dashed border-current px-1.5 py-0.5" : ""
      }`}
    >
      <Icon className={`${iconClass} shrink-0`} aria-hidden="true" />
      <span>{label}</span>
      {detail ? <span className="font-normal text-gray-500">{detail}</span> : null}
    </span>
  );
}

export function StatusBadge({ state, label, reason, size = "md" }: StatusBadgeProps) {
  return (
    <StatusGlyph
      visual={stepStatus[state]}
      label={label}
      reason={reason}
      size={size}
      dashed={state === "not_assessed"}
    />
  );
}

export function FundingBadge({ state }: { state: CyberFundingState }) {
  const visual = fundingStatus[state];
  return <StatusGlyph visual={visual} label={visual.label} />;
}

export function RepoStatusBadge({ status, reason }: { status: RepoStatus; reason: string }) {
  const visual = repoStatus[status];
  return (
    <StatusGlyph
      visual={visual}
      label={visual.label}
      reason={reason}
      dashed={status === "not_assessed"}
    />
  );
}
