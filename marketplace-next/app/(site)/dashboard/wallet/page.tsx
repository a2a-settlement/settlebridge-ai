import { Suspense } from "react";
import Wallet from "@/components/marketplace-pages/Wallet";

export const metadata = { title: "Wallet" };

export default function WalletPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center min-h-[40vh] text-gray-400">
          Loading…
        </div>
      }
    >
      <Wallet />
    </Suspense>
  );
}
