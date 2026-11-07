import type { WorkContext, WorkView } from "@/lib/cyberContracts";

export const WORK_CONTEXT_STORAGE_KEY = "sb.workContext";

export function isWorkView(value: string | null | undefined): value is WorkView {
  return value === "fund" || value === "solve";
}

function setOrDelete(params: URLSearchParams, key: string, value: string | null): void {
  if (value) params.set(key, value);
  else params.delete(key);
}

export function withContext(href: string, context: WorkContext): string {
  if (!href.startsWith("/") || href.startsWith("//")) return href;

  const hashAt = href.indexOf("#");
  const hash = hashAt === -1 ? "" : href.slice(hashAt);
  const beforeHash = hashAt === -1 ? href : href.slice(0, hashAt);
  const queryAt = beforeHash.indexOf("?");
  const path = queryAt === -1 ? beforeHash : beforeHash.slice(0, queryAt);
  const query = queryAt === -1 ? "" : beforeHash.slice(queryAt + 1);

  const params = new URLSearchParams(query);
  params.set("view", context.view);
  setOrDelete(params, "repo", context.repo);
  setOrDelete(params, "task", context.task);

  const qs = params.toString();
  return `${path}${qs ? `?${qs}` : ""}${hash}`;
}
