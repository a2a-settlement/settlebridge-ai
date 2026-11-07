"use client";

import {
  Suspense,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { WorkContext, WorkView } from "@/lib/cyberContracts";
import { WORK_CONTEXT_STORAGE_KEY, isWorkView, withContext } from "@/lib/workContext";

export { withContext };

interface WorkContextApi {
  context: WorkContext;
  setView: (view: WorkView) => void;
  setRepo: (repo: string | null) => void;
  setTask: (task: string | null) => void;
  clearRepo: () => void;
  clearTask: () => void;
}

const WorkContextReact = createContext<WorkContextApi | null>(null);

// router.replace does not emit popstate, and useSearchParams has to stay under
// Suspense, so the provider keeps an optimistic copy of the query string.
let publishedSearch: string | null = null;
const queryListeners = new Set<() => void>();

function normalizeSearch(search: string): string {
  return search.startsWith("?") ? search.slice(1) : search;
}

function publishQuery(search: string) {
  const next = normalizeSearch(search);
  if (publishedSearch === next) return;
  const previous =
    publishedSearch ??
    (typeof window === "undefined" ? "" : normalizeSearch(window.location.search));
  publishedSearch = next;
  if (previous !== next) {
    queryListeners.forEach((listener) => listener());
  }
}

function subscribeQuery(listener: () => void) {
  queryListeners.add(listener);
  const onPopState = () => {
    publishedSearch = null;
    listener();
  };
  window.addEventListener("popstate", onPopState);
  return () => {
    queryListeners.delete(listener);
    window.removeEventListener("popstate", onPopState);
  };
}

function readQuery(): string {
  if (publishedSearch !== null) return publishedSearch;
  return normalizeSearch(window.location.search);
}

function readQueryOnServer(): string {
  return "";
}

function subscribeStoredView(listener: () => void) {
  const onStorage = (event: StorageEvent) => {
    if (event.key === WORK_CONTEXT_STORAGE_KEY || event.key === null) listener();
  };
  window.addEventListener("storage", onStorage);
  return () => window.removeEventListener("storage", onStorage);
}

function readStoredView(): WorkView | null {
  try {
    const raw = localStorage.getItem(WORK_CONTEXT_STORAGE_KEY);
    return isWorkView(raw) ? raw : null;
  } catch {
    return null;
  }
}

function readStoredViewOnServer(): WorkView | null {
  return null;
}

function nonempty(value: string | null): string | null {
  return value && value.length > 0 ? value : null;
}

function writeStoredView(view: WorkView) {
  try {
    localStorage.setItem(WORK_CONTEXT_STORAGE_KEY, view);
  } catch {
    // Private browsing can throw when storage is disabled.
  }
}

function SearchParamsBridge() {
  const searchParams = useSearchParams();
  const serialized = searchParams.toString();
  useEffect(() => {
    publishQuery(serialized);
  }, [serialized]);
  return null;
}

function useWorkApi(): WorkContextApi {
  const router = useRouter();
  const pathname = usePathname();
  const query = useSyncExternalStore(subscribeQuery, readQuery, readQueryOnServer);
  const storedView = useSyncExternalStore(
    subscribeStoredView,
    readStoredView,
    readStoredViewOnServer
  );

  const params = new URLSearchParams(query);
  const viewParam = params.get("view");
  const urlView = isWorkView(viewParam) ? viewParam : null;
  const view = urlView ?? storedView ?? "fund";
  const repo = nonempty(params.get("repo"));
  const task = nonempty(params.get("task"));
  const context = useMemo<WorkContext>(() => ({ view, repo, task }), [view, repo, task]);

  const replaceContext = useCallback(
    (next: WorkContext) => {
      const nextParams = new URLSearchParams(readQuery());
      nextParams.set("view", next.view);
      if (next.repo) nextParams.set("repo", next.repo);
      else nextParams.delete("repo");
      if (next.task) nextParams.set("task", next.task);
      else nextParams.delete("task");
      const qs = nextParams.toString();
      publishQuery(qs);
      writeStoredView(next.view);
      const hash = window.location.hash;
      router.replace(`${pathname}${qs ? `?${qs}` : ""}${hash}`, { scroll: false });
    },
    [pathname, router]
  );

  return useMemo<WorkContextApi>(
    () => ({
      context,
      setView: (nextView) => replaceContext({ ...context, view: nextView }),
      setRepo: (nextRepo) => replaceContext({ ...context, repo: nonempty(nextRepo) }),
      setTask: (nextTask) => replaceContext({ ...context, task: nonempty(nextTask) }),
      clearRepo: () => replaceContext({ ...context, repo: null }),
      clearTask: () => replaceContext({ ...context, task: null }),
    }),
    [context, replaceContext]
  );
}

export function WorkContextProvider({ children }: { children: ReactNode }) {
  const api = useWorkApi();
  return (
    <WorkContextReact.Provider value={api}>
      <Suspense fallback={null}>
        <SearchParamsBridge />
      </Suspense>
      {children}
    </WorkContextReact.Provider>
  );
}

export function useWorkContext() {
  const api = useContext(WorkContextReact);
  if (!api) {
    throw new Error("useWorkContext must be used within WorkContextProvider");
  }
  return api;
}
