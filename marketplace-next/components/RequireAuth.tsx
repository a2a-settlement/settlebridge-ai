"use client";

import { useEffect, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/hooks/useAuth";
import { currentPath } from "@/lib/returnTo";

// GitHub returns here to finish signing in, before there is a session.
const PUBLIC_PATHS = new Set(["/dashboard/settings/github/callback"]);

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const isPublic = PUBLIC_PATHS.has(usePathname());

  useEffect(() => {
    if (!isPublic && !loading && !user) {
      router.replace(`/login?next=${encodeURIComponent(currentPath())}`);
    }
  }, [isPublic, loading, user, router]);

  if (isPublic) return <>{children}</>;
  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[40vh] text-gray-400">
        Loading…
      </div>
    );
  }
  if (!user) return null;
  return <>{children}</>;
}
