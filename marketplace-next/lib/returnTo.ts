const KEY = "sb_return_to";

/** A same-site path such as `/dashboard/github/setup?x=1`, or null for anything else. */
export function safeReturnPath(value: string | null | undefined): string | null {
  if (typeof value !== "string" || !value.startsWith("/")) return null;
  if (value.startsWith("//") || value.startsWith("/\\")) return null;
  if (/[\u0000-\u001f]/.test(value)) return null;
  return value;
}

export function currentPath(): string {
  return `${window.location.pathname}${window.location.search}`;
}

export function rememberReturnPath(path: string): void {
  const safe = safeReturnPath(path);
  if (safe) sessionStorage.setItem(KEY, safe);
}

export function takeReturnPath(): string | null {
  const value = sessionStorage.getItem(KEY);
  sessionStorage.removeItem(KEY);
  return safeReturnPath(value);
}
