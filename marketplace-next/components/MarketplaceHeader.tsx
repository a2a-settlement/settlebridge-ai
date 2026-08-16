"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, Menu, X, Shield } from "lucide-react";
import { useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import NotificationBell from "@/components/NotificationBell";
import WalletWidget from "@/components/WalletWidget";

const NAV = [
  { href: "/bounties", label: "Bounties" },
  { href: "/agents", label: "Agents" },
  { href: "/contracts", label: "Contracts" },
  { href: "/developers", label: "Developers" },
  { href: "/demo", label: "Demo" },
];

export default function MarketplaceHeader() {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);

  const isActive = (href: string) =>
    href === "/bounties"
      ? pathname === "/" || pathname.startsWith("/bounties")
      : pathname.startsWith(href);

  return (
    <header className="bg-navy-900 text-white sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          <div className="flex items-center gap-8">
            <Link href="/bounties" className="flex items-center gap-2 font-bold text-lg">
              <Shield className="w-6 h-6 text-money" />
              <span>SettleBridge</span>
            </Link>
            <nav className="hidden md:flex items-center gap-1 text-sm">
              {NAV.map(({ href, label }) => (
                <Link
                  key={href}
                  href={href}
                  className={`px-3 py-1.5 rounded-md transition ${
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

          <div className="flex items-center gap-3">
            {user ? (
              <>
                <WalletWidget />
                <NotificationBell />
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
                  href="/login"
                  className="text-gray-300 hover:text-white text-sm transition"
                >
                  Sign in
                </Link>
                <Link
                  href="/register"
                  className="px-4 py-2 bg-money text-navy-900 rounded-lg text-sm font-semibold hover:bg-money-dark transition"
                >
                  Get Started
                </Link>
              </div>
            )}
            <button
              type="button"
              className="md:hidden text-gray-400"
              onClick={() => setMobileOpen(!mobileOpen)}
              aria-label="Menu"
            >
              {mobileOpen ? <X /> : <Menu />}
            </button>
          </div>
        </div>
      </div>

      {mobileOpen && (
        <nav className="md:hidden border-t border-navy-800 px-4 py-3 space-y-1">
          {NAV.map(({ href, label }) => (
            <Link
              key={href}
              href={href}
              className={`block py-2 px-2 rounded-md text-sm ${
                isActive(href) ? "bg-white/10 text-white" : "text-gray-300"
              }`}
              onClick={() => setMobileOpen(false)}
            >
              {label}
            </Link>
          ))}
        </nav>
      )}
    </header>
  );
}
