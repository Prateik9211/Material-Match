import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import api from "@/lib/api";
import { LogOut, LayoutGrid, Shield, BookOpen, Rocket } from "lucide-react";
import { toast } from "sonner";

export default function Header({ variant = "app" }) {
  const { user, logout, setUser } = useAuth();
  const navigate = useNavigate();
  const [savingRegion, setSavingRegion] = useState(false);

  // 2026-02-08 — multi-region search. `preferred_region` holds a short
  // ISO code ("IN" | "US" | "AE"). Legacy values ("India" | "Global")
  // are normalised server-side on read so we only ever see codes here.
  const region = ["IN", "US", "AE"].includes(user?.preferred_region)
    ? user.preferred_region : "IN";

  const REGION_LABEL = { IN: "India", US: "United States", AE: "UAE" };
  const REGION_SHORT = { IN: "IN", US: "US", AE: "AE" };

  const switchRegion = async (next) => {
    if (!user || savingRegion || next === region) return;
    setSavingRegion(true);
    try {
      const { data } = await api.put("/users/me/preferences", { preferred_region: next });
      setUser({ ...user, preferred_region: data.preferred_region });
      toast.success(`Search region set to ${REGION_LABEL[data.preferred_region] || data.preferred_region}`);
    } catch {
      toast.error("Could not update region preference");
    } finally {
      setSavingRegion(false);
    }
  };

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

              <button
                onClick={() => navigate("/library")}
                className="hidden sm:inline-flex items-center gap-2 text-sm text-neutral-700 hover:text-black px-3 py-2"
                data-testid="nav-library"
              >
                <BookOpen className="w-4 h-4" strokeWidth={1.5} />
                Material Library
              </button>

              {user.role === "admin" && (
                <>
                  <button
                    onClick={() => navigate("/admin/affiliates")}
                    className="hidden sm:inline-flex items-center gap-2 text-sm text-neutral-700 hover:text-black px-3 py-2"
                    data-testid="nav-admin-affiliates"
                    title="Manage curated affiliate products"
                  >
                    <Shield className="w-4 h-4" strokeWidth={1.5} />
                    Affiliates
                  </button>
                  <button
                    onClick={() => navigate("/admin/knowledge-engine")}
                    className="hidden sm:inline-flex items-center gap-2 text-sm text-neutral-700 hover:text-black px-3 py-2"
                    data-testid="nav-admin-knowledge-engine"
                    title="Browse the MaterialMatch Library"
                  >
                    <Shield className="w-4 h-4" strokeWidth={1.5} />
                    Knowledge Engine
                  </button>
                  <button
                    onClick={() => navigate("/admin/studio")}
                    className="hidden sm:inline-flex items-center gap-2 text-sm text-neutral-700 hover:text-black px-3 py-2"
                    data-testid="nav-admin-studio"
                    title="MaterialMatch Studio — catalogue ingestion"
                  >
                    <Rocket className="w-4 h-4" strokeWidth={1.5} />
                    Studio
                  </button>
                  <button
                    onClick={() => navigate("/admin/scene-test")}
                    className="hidden sm:inline-flex items-center gap-2 text-sm text-neutral-700 hover:text-black px-3 py-2"
                    data-testid="nav-admin-scene-test"
                    title="SAM3 scene-segmentation debug tool"
                  >
                    <Shield className="w-4 h-4" strokeWidth={1.5} />
                    Scene Test
                  </button>
                  <button
                    onClick={() => navigate("/admin/users")}
                    className="hidden sm:inline-flex items-center gap-2 text-sm text-neutral-700 hover:text-black px-3 py-2"
                    data-testid="nav-admin-users"
                    title="Registered users (real signups only)"
                  >
                    <Shield className="w-4 h-4" strokeWidth={1.5} />
                    Users
                  </button>
                  <button
                    onClick={() => navigate("/admin/reviews")}
                    className="hidden sm:inline-flex items-center gap-2 text-sm text-neutral-700 hover:text-black px-3 py-2"
                    data-testid="nav-admin-reviews"
                    title="Submitted reviews inbox"
                  >
                    <Shield className="w-4 h-4" strokeWidth={1.5} />
                    Reviews
                  </button>
                </>
              )}

              {/* Leave-a-review CTA — shown to regular signed-in users only.
                  Admins have the AdminReviews management page instead;
                  submitting a testimonial doesn't belong in the admin nav. */}
              {user.role !== "admin" && (
                <button
                  onClick={() => navigate("/reviews/new")}
                  className="hidden sm:inline-flex items-center gap-2 text-sm text-neutral-700 hover:text-black px-3 py-2"
                  data-testid="nav-leave-review"
                  title="Share your feedback"
                >
                  <BookOpen className="w-4 h-4" strokeWidth={1.5} />
                  Leave a review
                </button>
              )}

              {/* Region search-scope selector — the ACTIVE region gates
                  every catalogue search + SerpApi similar-items call.
                  Cross-region records never merge into a single result. */}
              <div
                className="hidden sm:inline-flex items-center gap-1 bg-[#F5F1EC] rounded-full pl-3 pr-0.5 py-0.5 text-xs"
                data-testid="region-toggle"
                role="group"
                aria-label="Search region"
              >
                <span className="text-[10px] uppercase tracking-wider text-neutral-500 font-semibold">
                  Region
                </span>
                {["IN", "US", "AE"].map((r) => (
                  <button
                    key={r}
                    onClick={() => switchRegion(r)}
                    disabled={savingRegion}
                    aria-pressed={region === r}
                    aria-label={`Search catalogues for ${REGION_LABEL[r]}`}
                    title={`Search ${REGION_LABEL[r]} catalogues only — cross-region records never surface.`}
                    className={
                      "px-2.5 py-1.5 rounded-full font-medium transition-colors " +
                      (region === r
                        ? "bg-black text-white"
                        : "text-neutral-600 hover:text-black")
                    }
                    data-testid={`region-${r.toLowerCase()}-btn`}
                  >
                    {REGION_SHORT[r]}
                  </button>
                ))}
              </div>

              <div className="hidden md:flex items-center gap-2 px-3 py-1.5 rounded-full bg-[#F5F1EC]" data-testid="user-chip">
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
