import { Suspense } from "react";
import AgentsPage from "@/components/marketplace-pages/AgentsPage";

export const revalidate = 300;

export default function AgentsRoute() {
  return (
    <Suspense
      fallback={
        <div className="flex justify-center py-20 text-gray-400">Loading…</div>
      }
    >
      <AgentsPage />
    </Suspense>
  );
}
