import Link from "next/link";
import type { RepoCardProps, RepoStatus } from "@/lib/cyberContracts";
import { repoIcon as RepoIcon } from "@/lib/statusVocabulary";
import { RepoStatusBadge } from "@/components/StatusBadge";

const STATUS_ORDER: RepoStatus[] = [
  "needs_attention",
  "setup_needed",
  "task_in_progress",
  "paused",
  "not_assessed",
  "no_open_tasks",
];

export function sortRepoCards<T extends Pick<RepoCardProps, "status" | "repo">>(cards: T[]): T[] {
  return [...cards].sort(
    (a, b) =>
      STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status) ||
      a.repo.localeCompare(b.repo),
  );
}

export default function RepoCard({ repo, status, reason, href, meta }: RepoCardProps) {
  return (
    <Link
      href={href}
      className="block rounded-xl border border-gray-200 bg-white p-4 transition hover:border-navy-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-money"
    >
      <div className="flex items-center gap-2 text-sm font-semibold text-navy-900">
        <RepoIcon className="h-4 w-4 shrink-0 text-navy-700" aria-hidden="true" />
        <span className="truncate">{repo}</span>
      </div>
      <div className="mt-2">
        <RepoStatusBadge status={status} reason={reason} />
      </div>
      {meta ? <p className="mt-1 text-xs text-gray-500">{meta}</p> : null}
    </Link>
  );
}
