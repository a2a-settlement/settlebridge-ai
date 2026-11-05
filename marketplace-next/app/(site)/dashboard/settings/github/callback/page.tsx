import { Suspense } from "react";
import GitHubCallback from "@/components/marketplace-pages/GitHubCallback";

export const metadata = { title: "Link GitHub" };

export default function GitHubCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center min-h-[40vh] text-gray-400">
          Loading…
        </div>
      }
    >
      <GitHubCallback />
    </Suspense>
  );
}
