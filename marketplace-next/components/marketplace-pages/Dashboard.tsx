"use client";

import Link from "next/link";
import { Bell, RefreshCw, Server, Settings, Wallet, Wrench } from "lucide-react";
import { NotSetUpPanel } from "@/components/NotSetUpPanel";
import FunderHome from "@/components/marketplace-pages/FunderHome";
import SolverHome from "@/components/marketplace-pages/SolverHome";
import { useAuth } from "@/hooks/useAuth";
import { useView } from "@/hooks/useView";
import type { WorkView } from "@/lib/cyberContracts";
import { roleIncludesView } from "@/lib/funderView";

const SETUP_HREF: Record<WorkView, string> = {
  fund: "/dashboard/github/setup",
  solve: "/dashboard/github/solver",
};

export default function Dashboard() {
  const { user } = useAuth();
  const { view } = useView();

  if (!user) return null;

  const allowed = roleIncludesView(user.user_type, view);

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-navy-900">Dashboard</h1>
        <p className="text-gray-500 text-sm mt-1">Welcome back, {user.display_name}</p>
      </div>

      <div className="flex gap-8">
        <aside className="hidden lg:block w-48 flex-shrink-0">
          <nav className="space-y-1">
            <Link
              href="/contracts"
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-gray-700 hover:bg-gray-100"
            >
              <RefreshCw className="w-4 h-4" /> My Contracts
            </Link>
            <Link
              href="/dashboard/notifications"
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-gray-700 hover:bg-gray-100"
            >
              <Bell className="w-4 h-4" /> Notifications
            </Link>
            <Link
              href="/dashboard/wallet"
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-gray-700 hover:bg-gray-100"
            >
              <Wallet className="w-4 h-4" /> Wallet
            </Link>
            <Link
              href="/dashboard/github/solver"
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-gray-700 hover:bg-gray-100"
            >
              <Wrench className="w-4 h-4" /> Solver setup
            </Link>
            <Link
              href="/dashboard/settings"
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-gray-700 hover:bg-gray-100"
            >
              <Settings className="w-4 h-4" /> Settings
            </Link>
            {user.is_admin && (
              <a
                href="https://settlebridge.ai/app/"
                className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-gray-700 hover:bg-gray-100"
              >
                <Server className="w-4 h-4" /> Gateway console
              </a>
            )}
          </nav>
        </aside>

        <div className="flex-1 min-w-0">
          {allowed && view === "fund" && <FunderHome />}
          {allowed && view === "solve" && <SolverHome />}
          {!allowed && <NotSetUpPanel view={view} href={SETUP_HREF[view]} />}
        </div>
      </div>
    </div>
  );
}
