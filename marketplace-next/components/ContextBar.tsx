"use client";

import { X } from "lucide-react";
import type { ContextBarProps } from "@/lib/cyberContracts";
import { repoIcon as RepoIcon } from "@/lib/statusVocabulary";

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-money";

export function ContextBar({
  repo,
  task,
  taskLabel,
  onClearRepo,
  onClearTask,
}: ContextBarProps) {
  if (repo == null && task == null) return null;

  const taskText = taskLabel?.trim() ? taskLabel : task;

  return (
    <div className="border-t border-navy-800 bg-navy-950" role="group" aria-label="Current work">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-2 px-4 py-2 text-sm sm:px-6 lg:px-8">
        {repo != null ? (
          <span className="inline-flex max-w-full items-center gap-1.5 rounded-md bg-white/10 px-2 py-1 text-gray-200">
            <RepoIcon className="h-4 w-4 shrink-0" aria-hidden="true" />
            <span className="break-all">{repo}</span>
            <button
              type="button"
              onClick={onClearRepo}
              aria-label={`Clear repository ${repo}`}
              className={`inline-flex shrink-0 items-center gap-1 rounded-md px-1 text-gray-300 hover:text-white ${focusRing}`}
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Clear</span>
            </button>
          </span>
        ) : null}
        {task != null ? (
          <span className="inline-flex max-w-full items-center gap-1.5 rounded-md bg-white/10 px-2 py-1 text-gray-200">
            <span className="break-all">{taskText}</span>
            <button
              type="button"
              onClick={onClearTask}
              aria-label={`Clear task ${taskText ?? task}`}
              className={`inline-flex shrink-0 items-center gap-1 rounded-md px-1 text-gray-300 hover:text-white ${focusRing}`}
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Clear</span>
            </button>
          </span>
        ) : null}
      </div>
    </div>
  );
}
