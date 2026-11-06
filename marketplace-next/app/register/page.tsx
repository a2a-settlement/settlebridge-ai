import { Suspense } from "react";
import Register from "@/components/marketplace-pages/Register";

export const metadata = {
  title: "Register",
  robots: { index: false, follow: true },
};

export default function RegisterPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-navy-900 flex items-center justify-center text-gray-400">
          Loading…
        </div>
      }
    >
      <Register />
    </Suspense>
  );
}
