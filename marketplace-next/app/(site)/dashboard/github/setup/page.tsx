import { Suspense } from "react";
import GitHubSetup from "@/components/marketplace-pages/GitHubSetup";

export const metadata = { title: "Set up GitHub bounties" };

export default function GitHubSetupPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center min-h-[40vh] text-gray-400">
          Loading…
        </div>
      }
    >
      <GitHubSetup />
    </Suspense>
  );
}
