import { Link, Outlet, useNavigate, useLocation } from "react-router-dom";
import {
  LogOut,
  Menu,
  X,
  Shield,
  Activity,
  Users,
  ArrowRightLeft,
  FileText,
  BookOpen,
  Landmark,
  Bell,
  MessageSquare,
  Store,
} from "lucide-react";
import { useState } from "react";
import { useAuth } from "../hooks/useAuth";
import NotificationBell from "./NotificationBell";
import WalletWidget from "./WalletWidget";

const GATEWAY_NAV = [
  { to: "/", label: "Overview", icon: Activity },
  { to: "/agents", label: "Agents", icon: Users },
  { to: "/transactions", label: "Transactions", icon: ArrowRightLeft },
  { to: "/audit", label: "Audit", icon: FileText },
  { to: "/policies", label: "Policies", icon: BookOpen },
  { to: "/settlement", label: "Settlement", icon: Landmark },
  { to: "/alerts", label: "Alerts", icon: Bell },
];

export default function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);

  const isActive = (path: string) => {
    if (path === "/") return location.pathname === "/";
    return location.pathname.startsWith(path);
  };

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
              <nav className="hidden md:flex items-center gap-1 text-sm">
                {GATEWAY_NAV.map(({ to, label, icon: Icon }) => (
                  <Link
                    key={to}
                    to={to}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md transition ${
                      isActive(to)
                        ? "bg-white/10 text-white"
                        : "text-gray-400 hover:text-white hover:bg-white/5"
                    }`}
                  >
                    <Icon className="w-3.5 h-3.5" />
                    {label}
                  </Link>
                ))}
                <div className="w-px h-5 bg-gray-700 mx-1" />
                <Link
                  to="/marketplace"
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md transition ${
                    location.pathname.startsWith("/marketplace")
                      ? "bg-white/10 text-white"
                      : "text-gray-500 hover:text-gray-300 hover:bg-white/5"
                  }`}
                >
                  <Store className="w-3.5 h-3.5" />
                  Marketplace
                </Link>
              </nav>
            </div>

            <div className="flex items-center gap-3">
              {user ? (
                <>
                  <Link
                    to="/assist"
                    className="hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 text-sm text-gray-300 hover:text-white transition"
                    title="Gateway Assist"
                  >
                    <MessageSquare className="w-4 h-4" />
                  </Link>
                  <WalletWidget />
                  <NotificationBell />
                  <button
                    onClick={() => {
                      logout();
                      navigate("/login");
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
          <nav className="md:hidden border-t border-navy-800 px-4 py-3 space-y-1">
            {GATEWAY_NAV.map(({ to, label, icon: Icon }) => (
              <Link
                key={to}
                to={to}
                className={`flex items-center gap-2 py-2 px-2 rounded-md text-sm ${
                  isActive(to) ? "bg-white/10 text-white" : "text-gray-300 hover:text-white"
                }`}
                onClick={() => setMobileOpen(false)}
              >
                <Icon className="w-4 h-4" />
                {label}
              </Link>
            ))}
            <div className="border-t border-navy-800 my-2" />
            <Link
              to="/marketplace"
              className="flex items-center gap-2 py-2 px-2 rounded-md text-sm text-gray-400 hover:text-white"
              onClick={() => setMobileOpen(false)}
            >
              <Store className="w-4 h-4" />
              Marketplace
            </Link>
            {user && (
              <Link
                to="/assist"
                className="flex items-center gap-2 py-2 px-2 rounded-md text-sm text-gray-400 hover:text-white"
                onClick={() => setMobileOpen(false)}
              >
                <MessageSquare className="w-4 h-4" />
                Gateway Assist
              </Link>
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
