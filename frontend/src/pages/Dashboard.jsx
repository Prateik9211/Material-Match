import React, { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import Header from "@/components/Header";
import DemoModeBanner from "@/components/DemoModeBanner";
import api, { formatApiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Plus, FileText, ArrowUpRight, Sparkles, Clock } from "lucide-react";
import { toast } from "sonner";

const statusColor = {
  draft: "bg-neutral-100 text-neutral-600",
  queued: "bg-amber-50 text-amber-700",
  analyzing: "bg-blue-50 text-blue-700",
  completed: "bg-emerald-50 text-emerald-700",
  error: "bg-red-50 text-red-700",
};

export default function Dashboard() {
  const { user } = useAuth();
  const [projects, setProjects] = useState([]);
  const [reports, setReports] = useState([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  const fetchData = useCallback(async () => {
    try {
      const [pr, rp] = await Promise.all([
        api.get("/projects"),
        api.get("/reports"),
      ]);
      setProjects(pr.data);
      setReports(rp.data);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const projectRoute = (p) => `/projects/${p.id}/analysis`;

  const renderProjectsSection = () => {
    if (loading) {
      return (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-6">
          {[0, 1, 2].map((i) => (
            <div key={i} className="aspect-[4/3] rounded-2xl shimmer"></div>
          ))}
        </div>
      );
    }
    if (projects.length === 0) {
      return (
        <div className="bg-white border border-dashed border-black/10 rounded-2xl p-12 text-center" data-testid="empty-projects">
          <Sparkles className="w-8 h-8 text-neutral-300 mx-auto mb-3" strokeWidth={1.25} />
          <p className="text-neutral-500 mb-4">No projects yet — kick off your first material match.</p>
          <Link
            to="/projects/new"
            className="inline-flex items-center gap-2 bg-black text-white hover:bg-black/80 rounded-full px-5 py-2.5 text-sm font-medium"
            data-testid="empty-new-project-btn"
          >
            <Plus className="w-4 h-4" strokeWidth={1.5} /> Create project
          </Link>
        </div>
      );
    }
    return (
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-6">
        {projects.map((p) => (
          <button
            key={p.id}
            onClick={() => navigate(projectRoute(p))}
            className="text-left bg-white border border-black/5 rounded-2xl overflow-hidden shadow-soft hover:shadow-hover hover:-translate-y-1 transition-all duration-300"
            data-testid={`project-card-${p.id}`}
          >
            <div className="aspect-[16/10] bg-[#F3F2EE] relative grain">
              <div className="absolute inset-0 grid place-items-center">
                <div className="text-overline">{p.name?.slice(0, 2).toUpperCase() || "PR"}</div>
              </div>
              <span className={`absolute top-3 left-3 text-[10px] px-2 py-1 rounded-full font-medium ${statusColor[p.status] || statusColor.draft}`}>
                {p.status || "draft"}
              </span>
            </div>
            <div className="p-5">
              <h3 className="font-display font-semibold text-base mb-1 truncate">{p.name}</h3>
              <p className="text-xs text-neutral-500 truncate">{p.client_name || "No client"}</p>
              <div className="mt-3 flex items-center gap-1 text-xs text-neutral-400">
                <Clock className="w-3 h-3" strokeWidth={1.5} />
                {new Date(p.created_at).toLocaleDateString()}
              </div>
            </div>
          </button>
        ))}
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-[#F9F9F8]" data-testid="dashboard-page">
      <Header />

      <main className="max-w-7xl mx-auto px-6 py-12">
        <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-6 mb-12">
          <div>
            <div className="text-overline mb-2">Welcome back</div>
            <h1 className="font-display text-4xl sm:text-5xl font-bold tracking-tight">
              {user?.name || "Designer"}.
            </h1>
            <p className="text-neutral-500 mt-2">Pick up where you left off, or start a new project.</p>
          </div>
          <Link
            to="/projects/new"
            className="inline-flex items-center gap-2 bg-black text-white hover:bg-black/80 rounded-full px-6 py-3 font-medium transition-colors self-start"
            data-testid="new-project-btn"
          >
            <Plus className="w-4 h-4" strokeWidth={1.5} />
            New project
          </Link>
        </div>

        <DemoModeBanner className="mb-12" />

        {/* Stat cards */}
        <div className="grid sm:grid-cols-3 gap-6 mb-12">
          {[
            { label: "Active projects", value: projects.length, icon: Sparkles },
            { label: "Completed reports", value: reports.length, icon: FileText },
            { label: "Match accuracy", value: "Mock", icon: ArrowUpRight, sub: "Demo mode" },
          ].map((s, i) => (
            <div key={s.label} className="bg-white border border-black/5 rounded-2xl p-6 shadow-soft" data-testid={`stat-card-${i}`}>
              <div className="flex items-start justify-between mb-6">
                <span className="text-overline">{s.label}</span>
                <s.icon className="w-5 h-5 text-neutral-400" strokeWidth={1.25} />
              </div>
              <div className="font-display text-4xl font-bold tracking-tight">{s.value}</div>
              {s.sub && <div className="text-xs text-neutral-500 mt-1">{s.sub}</div>}
            </div>
          ))}
        </div>

        {/* Recent projects */}
        <section className="mb-16">
          <div className="flex items-baseline justify-between mb-6">
            <h2 className="font-display text-2xl font-semibold">Recent projects</h2>
          </div>

          {renderProjectsSection()}
        </section>

        {/* Recent reports */}
        <section>
          <div className="flex items-baseline justify-between mb-6">
            <h2 className="font-display text-2xl font-semibold">Recent reports</h2>
          </div>
          {reports.length === 0 ? (
            <div className="bg-white border border-dashed border-black/10 rounded-2xl p-8 text-center text-sm text-neutral-500" data-testid="empty-reports">
              Reports appear here after you complete an analysis.
            </div>
          ) : (
            <div className="bg-white border border-black/5 rounded-2xl divide-y divide-black/5 shadow-soft">
              {reports.map((r) => (
                <Link
                  key={r.id}
                  to={`/projects/${r.project_id}/report`}
                  className="flex items-center justify-between gap-4 p-5 hover:bg-[#F3F2EE]/40 transition-colors"
                  data-testid={`report-row-${r.id}`}
                >
                  <div className="flex items-center gap-4">
                    <div className="w-10 h-10 rounded-lg bg-[#F3F2EE] grid place-items-center">
                      <FileText className="w-5 h-5 text-neutral-700" strokeWidth={1.25} />
                    </div>
                    <div>
                      <div className="font-medium text-sm">{r.project_name}</div>
                      <div className="text-xs text-neutral-500">{r.client_name || "—"} · {new Date(r.created_at).toLocaleDateString()}</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="text-right hidden sm:block">
                      <div className="text-xs text-neutral-500">Top match</div>
                      <div className="font-display font-semibold">{Math.round((r.top_score || 0) * 100)}%</div>
                    </div>
                    <ArrowUpRight className="w-4 h-4 text-neutral-400" strokeWidth={1.5} />
                  </div>
                </Link>
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
