import { Link, Outlet, useNavigate } from "react-router-dom";
import {
  Search,
  Bell,
  Wallet,
  LogOut,
  Menu,
  X,
  Shield,
} from "lucide-react";
import { useState } from "react";
import { useAuth } from "../hooks/useAuth";
import NotificationBell from "./NotificationBell";
import WalletWidget from "./WalletWidget";

export default function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-navy-900 text-white sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-8">
              <Link to="/" className="flex items-center gap-2 font-bold text-lg">
                <Shield className="w-6 h-6 text-money" />
                <span>SettleBridge</span>
              </Link>
              <nav className="hidden md:flex items-center gap-6 text-sm">
                <Link
                  to="/bounties"
                  className="text-gray-300 hover:text-white transition"
                >
                  Bounties
                </Link>
                <Link
                  to="/agents"
                  className="text-gray-300 hover:text-white transition"
                >
                  Agents
                </Link>
                {user && (
                  <Link
                    to="/dashboard"
                    className="text-gray-300 hover:text-white transition"
                  >
                    Dashboard
                  </Link>
                )}
              </nav>
            </div>

            <div className="flex items-center gap-4">
              {user ? (
                <>
                  <WalletWidget />
                  <NotificationBell />
                  <Link
                    to="/bounties/new"
                    className="hidden sm:inline-flex items-center px-4 py-2 bg-money text-navy-900 rounded-lg text-sm font-semibold hover:bg-money-dark transition"
                  >
                    Post Bounty
                  </Link>
                  <button
                    onClick={() => {
                      logout();
                      navigate("/");
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
                    to="/login"
                    className="text-gray-300 hover:text-white text-sm transition"
                  >
                    Sign in
                  </Link>
                  <Link
                    to="/register"
                    className="px-4 py-2 bg-money text-navy-900 rounded-lg text-sm font-semibold hover:bg-money-dark transition"
                  >
                    Get Started
                  </Link>
                </div>
              )}
              <button
                className="md:hidden text-gray-400"
                onClick={() => setMobileOpen(!mobileOpen)}
              >
                {mobileOpen ? <X /> : <Menu />}
              </button>
            </div>
          </div>
        </div>

        {mobileOpen && (
          <nav className="md:hidden border-t border-navy-800 px-4 py-3 space-y-2">
            <Link
              to="/bounties"
              className="block text-gray-300 hover:text-white py-1"
              onClick={() => setMobileOpen(false)}
            >
              Bounties
            </Link>
            <Link
              to="/agents"
              className="block text-gray-300 hover:text-white py-1"
              onClick={() => setMobileOpen(false)}
            >
              Agents
            </Link>
            {user && (
              <>
                <Link
                  to="/dashboard"
                  className="block text-gray-300 hover:text-white py-1"
                  onClick={() => setMobileOpen(false)}
                >
                  Dashboard
                </Link>
                <Link
                  to="/bounties/new"
                  className="block text-money hover:text-money-dark py-1"
                  onClick={() => setMobileOpen(false)}
                >
                  Post Bounty
                </Link>
              </>
            )}
          </nav>
        )}
      </header>

      <main>
        <Outlet />
      </main>
    </div>
  );
}
