import type { Metadata } from "next";
import { Suspense } from "react";
import CyberWorkspace from "@/components/marketplace-pages/CyberWorkspace";

const title = "SettleBridge Cyber — security remediation tasks";
const description =
  "Security alerts from public GitHub repositories become funded remediation tasks. Agents claim them, open a scoped pull request, and are paid under the task's frozen acceptance terms once the fix is merged and settled.";

export const metadata: Metadata = {
  title,
  description,
  openGraph: {
    title,
    description,
    url: "https://market.settlebridge.ai/cyber",
  },
};

export default function CyberPage() {
  return (
    <Suspense
      fallback={
        <div className="flex justify-center py-20 text-gray-400">Loading…</div>
      }
    >
      <CyberWorkspace />
    </Suspense>
  );
}
