import { Suspense } from "react";
import BountyFeed from "@/components/marketplace-pages/BountyFeed";

export const revalidate = 60;

export default function BountiesPage() {
  return (
    <Suspense
      fallback={
        <div className="flex justify-center py-20 text-gray-400">Loading…</div>
      }
    >
      <BountyFeed />
    </Suspense>
  );
}
