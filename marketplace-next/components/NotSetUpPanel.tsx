import Link from "next/link";
import type { NotSetUpPanelProps, WorkView } from "@/lib/cyberContracts";
import { roleSwitch } from "@/lib/statusVocabulary";

const COPY: Record<WorkView, { body: string; action: string }> = {
  fund: {
    body: "This view is for funders and maintainers. Use it to connect a repository, check a balance, and fund tasks for a solver.",
    action: "Set up funding",
  },
  solve: {
    body: "This view is for solvers and challengers. Use it to claim a funded task, submit a patch, and challenge a result.",
    action: "Set up solving",
  },
};

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-money";

export function NotSetUpPanel({ view, href }: NotSetUpPanelProps) {
  const copy = COPY[view];
  const entry = roleSwitch[view];
  const Icon = entry.icon;

  return (
    <section className="rounded-xl border border-gray-200 bg-white p-6">
      <h2 className="text-lg font-semibold text-navy-900">Not set up yet</h2>
      <p className="mt-2 inline-flex items-center gap-1.5 text-sm font-medium text-navy-800">
        <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
        {entry.label}
      </p>
      <p className="mt-2 text-sm leading-relaxed text-gray-600">{copy.body}</p>
      <Link
        href={href}
        className={`mt-4 inline-flex items-center rounded-lg bg-money px-4 py-2 text-sm font-semibold text-navy-900 transition hover:bg-money-dark ${focusRing}`}
      >
        {copy.action}
      </Link>
    </section>
  );
}
