import { Suspense } from "react";
import SolverSetup from "@/components/marketplace-pages/SolverSetup";

export const metadata = { title: "Solver setup" };

export default function SolverSetupPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center min-h-[40vh] text-gray-400">
          Loading…
        </div>
      }
    >
      <SolverSetup />
    </Suspense>
  );
}
