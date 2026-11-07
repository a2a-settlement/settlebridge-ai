import Link from "next/link";
import type { NextAction, NextActionPanelProps } from "@/lib/cyberContracts";

const linkClass =
  "mt-3 inline-flex items-center rounded-lg bg-money px-4 py-2 text-sm font-semibold text-navy-900 transition hover:bg-money-dark focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-money";

function destination(href: string): "internal" | "external" | null {
  if (href.startsWith("/") && !href.startsWith("//")) return "internal";
  if (href.startsWith("https://") || href.startsWith("http://")) return "external";
  return null;
}

function PrimaryLink({ link }: { link: NonNullable<NextAction["link"]> }) {
  const kind = destination(link.href);
  if (kind === "internal") {
    return (
      <Link href={link.href} className={linkClass}>
        {link.label}
      </Link>
    );
  }
  if (kind === "external") {
    return (
      <a href={link.href} className={linkClass} target="_blank" rel="noopener noreferrer">
        {link.label}
      </a>
    );
  }
  return null;
}

export function NextActionPanel({ action }: NextActionPanelProps) {
  return (
    <section className="rounded-xl border border-gray-200 bg-white p-4" aria-label="Next action">
      <h2 className="text-base font-semibold text-navy-900">{action.headline}</h2>
      <p className="mt-1 text-sm leading-relaxed text-gray-700 break-words">{action.sentence}</p>
      {action.link ? <PrimaryLink link={action.link} /> : null}
    </section>
  );
}
