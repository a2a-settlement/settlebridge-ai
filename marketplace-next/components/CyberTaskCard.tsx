import Link from "next/link";
import { Coins } from "lucide-react";
import type { CyberTask } from "@/lib/types";

const SOURCE_LABELS: Record<string, string> = {
  dependabot: "Dependabot",
  code_scanning: "Code scanning",
  security_label: "Security task",
};

const FUNDING_LABELS: Record<string, string> = {
  balance_checked: "Balance checked — not reserved",
  funds_held: "Funds held for active claim",
  payment_pending: "Payment pending",
  payment_review: "Payment under review — funds held",
  paid: "Paid",
  not_paid: "Not paid — refunded or cancelled",
};

const WORK_LABELS: Record<string, string> = {
  available: "Available",
  claimed: "Claimed — in progress",
  pr_submitted: "PR submitted",
  evaluating: "Evaluating",
  needs_maintainer_review: "Needs maintainer review",
  awaiting_merge: "Accepted — awaiting merge",
  settling: "Merged — settlement in progress",
  completed: "Completed",
  cancelled: "Cancelled",
};

const TRIGGER_LABELS: Record<string, string> = {
  merge_with_checks: "Paid on merge with passing checks",
  merged_and_alert_fixed: "Paid after GitHub confirms the alert is fixed",
};

const SOURCE_STYLES: Record<string, string> = {
  dependabot: "bg-money/10 text-money-dark",
  code_scanning: "bg-navy-100 text-navy-800",
  security_label: "bg-gray-100 text-gray-700",
};

const WORK_STYLES: Record<string, string> = {
  available: "bg-money/10 text-money-dark",
  claimed: "bg-blue-100 text-blue-700",
  pr_submitted: "bg-purple-100 text-purple-700",
  evaluating: "bg-indigo-100 text-indigo-700",
  needs_maintainer_review: "bg-yellow-100 text-yellow-800",
  awaiting_merge: "bg-navy-100 text-navy-800",
  settling: "bg-orange-100 text-orange-800",
  completed: "bg-green-100 text-green-800",
  cancelled: "bg-gray-100 text-gray-500",
};

const externalLinkClass =
  "text-navy-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-money rounded-sm";

function rawEnum(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function enumLabel(map: Record<string, string>, value: unknown): string {
  const raw = rawEnum(value);
  if (!raw) return "";
  return map[raw] ?? raw;
}

function pillClass(map: Record<string, string>, value: unknown): string {
  const raw = rawEnum(value);
  return (raw && map[raw]) || "bg-gray-100 text-gray-600";
}

function rewardLabel(amount: unknown): string {
  if (typeof amount === "number" && Number.isFinite(amount)) return `${amount} ATE (test)`;
  if (typeof amount === "string" && amount.trim()) return `${amount.trim()} ATE (test)`;
  return "ATE (test)";
}

function plainNumber(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "string" && value.trim()) return value.trim();
  return "";
}

function numberedLabel(prefix: string, value: unknown): string {
  const number = plainNumber(value);
  return number ? `${prefix} #${number}` : prefix;
}

function httpUrl(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const url = new URL(value);
    if (url.protocol === "http:" || url.protocol === "https:") return url.href;
  } catch {
    return null;
  }
  return null;
}

function bountyHref(id: unknown): string {
  if (typeof id === "string" && id.trim()) return `/bounties/${encodeURIComponent(id)}`;
  if (typeof id === "number" && Number.isFinite(id)) return `/bounties/${id}`;
  return "/bounties";
}

interface Props {
  task: CyberTask;
}

export default function CyberTaskCard({ task }: Props) {
  const issueHref = httpUrl(task.issue_url);
  const prHref = httpUrl(task.pr_url);
  const source = enumLabel(SOURCE_LABELS, task.source);
  const work = enumLabel(WORK_LABELS, task.work_state);
  const funding = enumLabel(FUNDING_LABELS, task.funding_state);
  const trigger = enumLabel(TRIGGER_LABELS, task.payment_trigger);
  const reputation = plainNumber(task.eligibility?.min_reputation);
  const kya = plainNumber(task.eligibility?.min_kya);
  const repo = typeof task.repo === "string" ? task.repo : "";
  const title = typeof task.title === "string" ? task.title : "";
  const showPr = prHref != null || plainNumber(task.pr_number) !== "";

  return (
    <article className="bg-white border border-gray-200 rounded-xl p-5 hover:shadow-lg hover:border-navy-300 transition-all">
      <div className="flex items-start justify-between gap-3 mb-3">
        <h3 className="font-semibold text-navy-900 line-clamp-2 leading-snug min-w-0">
          <Link
            href={bountyHref(task.bounty_id)}
            className="hover:text-navy-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-money rounded-sm"
          >
            {title}
          </Link>
        </h3>
        <div className="flex-shrink-0 flex items-center gap-1.5 bg-money/10 text-money-dark px-3 py-1.5 rounded-lg font-bold text-sm whitespace-nowrap">
          <Coins className="w-4 h-4" aria-hidden="true" />
          {rewardLabel(task.reward_ate)}
        </div>
      </div>

      <p className="text-sm text-gray-600 flex flex-wrap items-center gap-x-2 gap-y-1 mb-4 break-words">
        {repo ? <span className="font-medium text-navy-900">{repo}</span> : null}
        {issueHref ? (
          <a
            href={issueHref}
            target="_blank"
            rel="noopener noreferrer"
            className={externalLinkClass}
          >
            {numberedLabel("Issue", task.issue_number)}
          </a>
        ) : (
          <span>{numberedLabel("Issue", task.issue_number)}</span>
        )}
        {showPr ? (
          prHref ? (
            <a
              href={prHref}
              target="_blank"
              rel="noopener noreferrer"
              className={externalLinkClass}
            >
              {numberedLabel("PR", task.pr_number)}
            </a>
          ) : (
            <span>{numberedLabel("PR", task.pr_number)}</span>
          )
        ) : null}
      </p>

      <div className="flex flex-wrap items-center gap-2">
        {source ? (
          <span
            className={`text-xs font-medium px-2.5 py-1 rounded-full ${pillClass(SOURCE_STYLES, task.source)}`}
          >
            {source}
          </span>
        ) : null}
        {work ? (
          <span
            className={`text-xs font-medium px-2.5 py-1 rounded-full ${pillClass(WORK_STYLES, task.work_state)}`}
          >
            {work}
          </span>
        ) : null}
      </div>

      {funding ? <p className="text-sm text-gray-600 mt-3">{funding}</p> : null}
      {trigger ? <p className="text-sm text-gray-500 mt-1">{trigger}</p> : null}
      {reputation ? (
        <p className="text-sm text-navy-900 mt-3">Needs reputation {reputation}+</p>
      ) : null}

      <details className="mt-3 text-sm text-gray-600">
        <summary className="cursor-pointer w-fit font-medium text-navy-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-money rounded-sm">
          Details
        </summary>
        <div className="mt-2 space-y-1">
          <p>Minimum KYA level {kya}</p>
          <p>Reputation (EMA) {reputation}</p>
        </div>
      </details>
    </article>
  );
}
