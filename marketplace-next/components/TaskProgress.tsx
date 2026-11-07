import type { ProgressStep, TaskProgressProps } from "@/lib/cyberContracts";
import { stepLabels } from "@/lib/statusLabels";
import { StatusBadge } from "@/components/StatusBadge";

function StepView({ step, size }: { step: ProgressStep; size: "sm" | "md" }) {
  const statusLabel = stepLabels[step.state].label;
  const detail = step.detail?.trim();
  const reason =
    detail && detail.toLowerCase() !== statusLabel.toLowerCase() ? detail : undefined;
  const current = step.state === "in_progress" || step.state === "needs_decision";

  return (
    <div className="min-w-0" aria-current={current ? "step" : undefined}>
      <p
        className={`font-semibold text-navy-900 ${
          size === "sm" ? "text-xs" : "text-sm"
        }`}
      >
        {step.label}
      </p>
      <div className="mt-1">
        <StatusBadge state={step.state} label={statusLabel} reason={reason} size={size} />
      </div>
    </div>
  );
}

export function TaskProgress({ steps, variant }: TaskProgressProps) {
  if (variant === "compact") {
    return (
      <ol className="m-0 flex list-none flex-wrap gap-x-4 gap-y-2 p-0" aria-label="Task progress">
        {steps.map((step) => (
          <li key={step.id} className="min-w-0 max-w-full">
            <StepView step={step} size="sm" />
          </li>
        ))}
      </ol>
    );
  }

  return (
    <ol
      className="m-0 flex list-none flex-col p-0 md:flex-row md:items-start md:gap-3"
      aria-label="Task progress"
    >
      {steps.map((step) => (
        <li
          key={step.id}
          className="min-w-0 flex-1 border-l border-gray-200 py-2 pl-3 md:border-l-0 md:border-t-2 md:py-0 md:pl-0 md:pt-3"
        >
          <StepView step={step} size="md" />
        </li>
      ))}
    </ol>
  );
}
