import React from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { LogOut, LayoutGrid } from "lucide-react";

export default function Header({ variant = "app" }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <header className="sticky top-0 z-50 glass border-b border-black/5" data-testid="app-header">
      <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
        <Link to={user ? "/dashboard" : "/"} className="flex items-center gap-2 group" data-testid="brand-logo">
          <div className="w-7 h-7 rounded-md bg-black grid place-items-center">
            <div className="w-3 h-3 rounded-sm bg-white"></div>
          </div>
          <span className="font-display font-bold tracking-tight text-lg">
            MaterialMatch<span className="text-neutral-400">.AI</span>
          </span>
        </Link>

        <nav className="flex items-center gap-3">
          {user ? (
            <>
              <button
                onClick={() => navigate("/dashboard")}
                className="hidden sm:inline-flex items-center gap-2 text-sm text-neutral-700 hover:text-black px-3 py-2"
                data-testid="nav-dashboard"
              >
                <LayoutGrid className="w-4 h-4" strokeWidth={1.5} />
                Dashboard
              </button>
              <div className="hidden md:flex items-center gap-2 px-3 py-1.5 rounded-full bg-[#F3F2EE]" data-testid="user-chip">
                <div className="w-6 h-6 rounded-full bg-black text-white grid place-items-center text-xs font-medium">
                  {(user.name || user.email || "U")[0].toUpperCase()}
                </div>
                <span className="text-xs text-neutral-700">{user.name || user.email}</span>
              </div>
              <button
                onClick={async () => { await logout(); navigate("/"); }}
                className="inline-flex items-center gap-2 text-sm text-neutral-700 hover:text-black px-3 py-2"
                data-testid="logout-btn"
              >
                <LogOut className="w-4 h-4" strokeWidth={1.5} />
                Sign out
              </button>
            </>
          ) : (
            <>
              <Link to="/auth" className="text-sm text-neutral-700 hover:text-black px-3 py-2" data-testid="header-signin">
                Sign in
              </Link>
              <Link
                to="/auth?mode=register"
                className="text-sm bg-black text-white hover:bg-black/80 rounded-full px-5 py-2.5 font-medium transition-colors"
                data-testid="header-signup"
              >
                Get started
              </Link>
            </>
          )}
        </nav>
      </div>
    </header>
  );
}
