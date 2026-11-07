import { BadgeAlert } from "lucide-react";
import type { FindingCardProps } from "@/lib/cyberContracts";
import {
  citationView,
  evidencePresentation,
  failureClassView,
  findingKindLabel,
  findingStatusView,
  severityPresentation,
} from "@/lib/challengeView";
import { StatusBadge } from "@/components/StatusBadge";

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-money rounded-sm";

const linkClass = `text-sm font-medium text-navy-700 hover:underline ${focusRing}`;

function EvidenceLink({ href, label }: { href: string; label: string }) {
  const external = href.startsWith("http://") || href.startsWith("https://");
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

export function FindingCard({ finding }: FindingCardProps) {
  const status = findingStatusView(finding.status);
  const failureClass = failureClassView(finding.failure_class);
  const severity = severityPresentation(finding.advisory_severity);
  const citation = citationView(finding.oracle_citation);
  const evidence = evidencePresentation(finding.evidence_url);
  const quote = citation?.quote.trim() ?? "";

  return (
    <article className="rounded-xl border border-gray-200 bg-white p-5">
      <h3 className="break-words text-base font-semibold text-navy-900">{finding.title}</h3>
      <p className="mt-1 text-sm text-gray-500">{findingKindLabel(finding.kind)}</p>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        <StatusBadge state={status.state} label={status.label} />
        {severity.rated ? (
          <span className="inline-flex items-center gap-1.5 text-sm font-medium text-navy-800">
            <BadgeAlert className="h-4 w-4 shrink-0" aria-hidden="true" />
            <span>{severity.label}</span>
          </span>
        ) : (
          <StatusBadge state="not_assessed" label={severity.label} />
        )}
      </div>
      <p className="mt-3 text-sm text-gray-700">
        <span className="font-medium text-navy-900">Failure class: </span>
        {failureClass.label}
      </p>
      {citation ? (
        <figure className="mt-3">
          <figcaption className="break-words text-sm text-gray-600">
            {citation.sourceLabel}
            {citation.reference ? ` · ${citation.reference}` : ""}
          </figcaption>
          {quote ? (
            <blockquote className="mt-1 break-words border-l-2 border-gray-200 pl-3 text-sm leading-relaxed text-gray-800">
              {quote}
            </blockquote>
          ) : null}
        </figure>
      ) : (
        <p className="mt-3 text-sm text-gray-500">No citation recorded</p>
      )}
      <p className="mt-3">
        {evidence.href ? (
          <EvidenceLink href={evidence.href} label={evidence.label} />
        ) : (
          <span className="text-sm text-gray-500">{evidence.label}</span>
        )}
      </p>
    </article>
  );
}
