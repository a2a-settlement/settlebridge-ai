"use client";

import { type KeyboardEvent } from "react";
import type { RoleSwitchProps, WorkView } from "@/lib/cyberContracts";
import { roleSwitch } from "@/lib/statusVocabulary";

const VIEWS: readonly WorkView[] = ["fund", "solve"];

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-money focus-visible:relative focus-visible:z-10";

export function RoleSwitch({ value, onChange, className }: RoleSwitchProps) {
  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const delta =
      event.key === "ArrowRight" || event.key === "ArrowDown"
        ? 1
        : event.key === "ArrowLeft" || event.key === "ArrowUp"
          ? -1
          : 0;
    if (delta === 0) return;
    event.preventDefault();
    const next = (index + delta + VIEWS.length) % VIEWS.length;
    const view = VIEWS[next];
    if (!view) return;
    onChange(view);
    const buttons = event.currentTarget.parentElement?.querySelectorAll("button");
    buttons?.[next]?.focus();
  }

  return (
    <div
      role="group"
      aria-label="Acting as"
      className={`inline-flex flex-wrap items-stretch gap-1 rounded-lg bg-white/10 p-1 ${className ?? ""}`}
    >
      {VIEWS.map((view, index) => {
        const active = value === view;
        const entry = roleSwitch[view];
        const Icon = entry.icon;
        return (
          <button
            key={view}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(view)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={`inline-flex min-w-0 flex-1 items-center justify-center gap-1.5 whitespace-normal rounded-md px-2.5 py-1.5 text-center text-sm leading-snug transition ${focusRing} ${
              active
                ? "bg-money/10 text-money ring-1 ring-inset ring-money"
                : "text-gray-300 hover:bg-white/5 hover:text-white"
            }`}
          >
            <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
            <span>{entry.label}</span>
          </button>
        );
      })}
    </div>
  );
}
