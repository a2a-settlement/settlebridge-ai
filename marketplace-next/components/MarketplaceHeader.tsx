"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, Menu, X, Shield } from "lucide-react";
import { useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import { useView } from "@/hooks/useView";
import { useWorkContext } from "@/hooks/useWorkContext";
import { withContext } from "@/lib/workContext";
import { ContextBar } from "@/components/ContextBar";
import NotificationBell from "@/components/NotificationBell";
import { RoleSwitch } from "@/components/RoleSwitch";
import WalletWidget from "@/components/WalletWidget";

const NAV = [
  { href: "/cyber", label: "Cyber" },
  { href: "/bounties", label: "Bounties" },
  { href: "/agents", label: "Agents" },
  { href: "/contracts", label: "Contracts" },
  { href: "/developers", label: "Developers" },
  { href: "/demo", label: "Demo" },
];

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-money";

export default function MarketplaceHeader() {
  const { user, logout } = useAuth();
  const { view, setView } = useView();
  const { context, clearRepo, clearTask } = useWorkContext();
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);

  const isActive = (href: string) =>
    href === "/bounties"
      ? pathname === "/" || pathname.startsWith("/bounties")
      : pathname.startsWith(href);

  const link = (href: string) => withContext(href, context);

  return (
    <header className="bg-navy-900 text-white sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex min-h-16 flex-wrap items-center justify-between">
          <div className="flex items-center gap-8">
            <Link href={link("/bounties")} className="flex items-center gap-2 font-bold text-lg">
              <Shield className="w-6 h-6 text-money" />
              <span>SettleBridge</span>
            </Link>
            <nav className="hidden md:flex items-center gap-1 text-sm">
              {NAV.map(({ href, label }) => (
                <Link
                  key={href}
                  href={link(href)}
                  aria-current={isActive(href) ? "page" : undefined}
                  className={`px-3 py-1.5 rounded-md transition ${focusRing} ${
                    isActive(href)
                      ? "bg-white/10 text-white"
                      : "text-gray-400 hover:text-white hover:bg-white/5"
                  }`}
                >
                  {label}
                </Link>
              ))}
            </nav>
          </div>

          <div className="flex flex-wrap items-center justify-end gap-3">
            <div className="hidden md:block">
              <RoleSwitch value={view} onChange={setView} />
            </div>
            {user ? (
              <>
                <WalletWidget />
                <NotificationBell />
                <Link
                  href={link("/dashboard")}
                  aria-current={isActive("/dashboard") ? "page" : undefined}
                  className={`px-3 py-1.5 rounded-md text-sm transition ${focusRing} ${
                    isActive("/dashboard")
                      ? "bg-white/10 text-white"
                      : "text-gray-400 hover:text-white hover:bg-white/5"
                  }`}
                >
                  Dashboard
                </Link>
                <button
                  type="button"
                  onClick={() => {
                    logout();
                    window.location.href = "/login";
                  }}
                  className="text-gray-400 hover:text-white transition"
                  title="Logout"
                >
                  <LogOut className="w-5 h-5" />
                </button>
              </>
            ) : (
              <div className="flex items-center gap-3">
                <Link
                  href={link("/login")}
                  className="text-gray-300 hover:text-white text-sm transition"
                >
                  Sign in
                </Link>
                <Link
                  href={link("/register")}
                  className="px-4 py-2 bg-money text-navy-900 rounded-lg text-sm font-semibold hover:bg-money-dark transition"
                >
                  Get Started
                </Link>
              </div>
            )}
            <button
              type="button"
              className={`md:hidden text-gray-400 rounded-md ${focusRing}`}
              onClick={() => setMobileOpen(!mobileOpen)}
              aria-label="Menu"
              aria-expanded={mobileOpen}
              aria-controls="mobile-nav"
            >
              {mobileOpen ? <X /> : <Menu />}
            </button>
          </div>
        </div>
      </div>

      <ContextBar
        repo={context.repo}
        task={context.task}
        onClearRepo={clearRepo}
        onClearTask={clearTask}
      />

      <nav
        id="mobile-nav"
        hidden={!mobileOpen}
        className="md:hidden border-t border-navy-800 px-4 py-3 space-y-1"
      >
        <RoleSwitch value={view} onChange={setView} className="w-full" />
        {user && (
          <Link
            href={link("/dashboard")}
            aria-current={isActive("/dashboard") ? "page" : undefined}
            className={`block py-2 px-2 rounded-md text-sm ${focusRing} ${
              isActive("/dashboard") ? "bg-white/10 text-white" : "text-gray-300"
            }`}
            onClick={() => setMobileOpen(false)}
          >
            Dashboard
          </Link>
        )}
        {NAV.map(({ href, label }) => (
          <Link
            key={href}
            href={link(href)}
            aria-current={isActive(href) ? "page" : undefined}
            className={`block py-2 px-2 rounded-md text-sm ${focusRing} ${
              isActive(href) ? "bg-white/10 text-white" : "text-gray-300"
            }`}
            onClick={() => setMobileOpen(false)}
          >
            {label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
