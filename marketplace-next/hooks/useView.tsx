"use client";

import { useWorkContext } from "@/hooks/useWorkContext";

export function useView() {
  const { context, setView } = useWorkContext();
  return { view: context.view, setView };
}
