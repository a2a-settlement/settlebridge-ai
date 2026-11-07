import {
  AlertCircle,
  Ban,
  CheckCircle2,
  Circle,
  CircleDashed,
  CircleDot,
  CircleHelp,
  ScanSearch,
  ShieldCheck,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { CHECKS_CAPTION, type ChallengeResultProps } from "@/lib/cyberContracts";
import {
  buildChallengeView,
  externalHref,
  type RoleIconName,
} from "@/lib/challengeView";
import { FindingCard } from "@/components/FindingCard";
import { StatusBadge } from "@/components/StatusBadge";
import { stepStatus } from "@/lib/statusVocabulary";

const ICONS = {
  AlertCircle,
  Ban,
  CheckCircle2,
  Circle,
  CircleDashed,
  CircleDot,
  CircleHelp,
  ScanSearch,
  ShieldCheck,
  Wrench,
} satisfies Record<RoleIconName, LucideIcon>;

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-money rounded-sm";

const linkClass = `text-sm font-medium text-navy-700 hover:underline ${focusRing}`;

const sectionTitle = "text-lg font-semibold text-navy-900";

function ReportLink({ href, label }: { href: string; label: string }) {
  const external = externalHref(href);
  return (
    <a
      href={href}
      className={linkClass}
      {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
    >
      {label}
    </a>
  );
}

export function ChallengeResult({ challenge }: ChallengeResultProps) {
  const view = buildChallengeView(challenge);

  return (
    <div className="space-y-8">
      <section aria-labelledby="challenge-task">
        <h2 id="challenge-task" className={sectionTitle}>
          Task
        </h2>
        <div className="mt-3">
          <StatusBadge state={view.taskState} label={view.taskLabel} reason={view.taskReason} />
        </div>
      </section>

      <section aria-labelledby="challenge-property">
        <h2 id="challenge-property" className={sectionTitle}>
          Property
        </h2>
        <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-relaxed text-gray-700">
          {view.propertyText}
        </p>
        <h3 className="mt-4 text-sm font-semibold text-navy-900">Scope</h3>
        <p className="mt-1 whitespace-pre-wrap break-words text-sm leading-relaxed text-gray-600">
          {view.scopeNote}
        </p>
      </section>

      <section aria-labelledby="challenge-roles">
        <h2 id="challenge-roles" className={sectionTitle}>
          Roles
        </h2>
        <ul className="mt-3 divide-y divide-gray-100 border-y border-gray-100">
          {view.rows.map((row) => {
            const Icon = ICONS[row.icon];
            const tone = stepStatus[row.state].textClass;
            return (
              <li key={row.role} className="flex items-start gap-3 py-3">
                <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${tone}`} aria-hidden="true" />
                <div className="min-w-0">
                  <p className="break-words text-sm font-medium text-navy-900">
                    {row.roleLabel}
                    <span className="font-normal text-gray-600"> · {row.operatorLabel}</span>
                    {row.platformLabel ? (
                      <span className="font-normal text-gray-500"> · {row.platformLabel}</span>
                    ) : null}
                  </p>
                  <p className={`mt-1 break-words text-sm ${tone}`}>{row.text}</p>
                  {row.reason ? <p className="mt-1 break-words text-sm text-gray-600">{row.reason}</p> : null}
                </div>
              </li>
            );
          })}
        </ul>
        <p className="mt-4 text-sm leading-relaxed text-gray-600">{CHECKS_CAPTION}</p>
        {view.reportHref && view.reportLabel ? (
          <p className="mt-3">
            <ReportLink href={view.reportHref} label={view.reportLabel} />
          </p>
        ) : view.reportLabel ? (
          <p className="mt-3 text-sm text-gray-500">{view.reportLabel}</p>
        ) : null}
      </section>

      <section aria-labelledby="challenge-findings">
        <h2 id="challenge-findings" className={sectionTitle}>
          Findings
        </h2>
        {view.findings.length === 0 ? (
          <p className="mt-3 text-sm text-gray-600">No findings recorded.</p>
        ) : (
          <ul className="mt-3 space-y-4">
            {view.findings.map((item) => (
              <li key={item.id}>
                <FindingCard finding={item.finding} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
