import React, { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import Header from "@/components/Header";
import api, { formatApiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Plus, FileText, ArrowUpRight, Sparkles, Clock, PlayCircle, Camera, Layers, BookOpen, ShoppingBag, ListChecks, Trash2 } from "lucide-react";
import { toast } from "sonner";

const statusColor = {
  draft: "bg-stone-panel text-warm-grey",
  queued: "bg-ochre-soft text-ochre",
  analyzing: "bg-sage-soft text-sage",
  completed: "bg-sage-soft text-sage",
  error: "bg-red-50 text-red-700",
};

const WORKFLOW_STEPS = [
  { icon: Camera, title: "Upload a reference", body: "A Pinterest pin or an interior photograph." },
  { icon: Layers, title: "Detect materials & products", body: "Zones, finishes, colour and regional sourcing context." },
  { icon: BookOpen, title: "Match catalogues", body: "Upload a supplier PDF or search your Material Library." },
  { icon: ShoppingBag, title: "Discover products", body: "Lighting, furniture, decor — with curated regional options." },
  { icon: ListChecks, title: "Build sourceable shortlist", body: "Walk into vendor meetings prepared." },
];

function WelcomePanel({ onDemo, onCreate, onLibrary }) {
  return (
    <div className="bg-white border border-stone-border-soft rounded-3xl p-10 sm:p-12 shadow-soft" data-testid="welcome-panel">
      <div className="text-overline mb-3">Welcome to MaterialMatch</div>
      <h2 className="font-display text-3xl sm:text-4xl font-bold tracking-tight text-charcoal max-w-2xl">
        Turn inspiration into sourceable materials.
      </h2>
      <p className="text-warm-grey mt-3 max-w-2xl">
        Explore the demo project, start a new project, or begin building your reusable Material Library.
      </p>
      <div className="mt-6 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={onDemo}
          className="inline-flex items-center gap-2 bg-charcoal text-paper hover:bg-charcoal/85 rounded-full px-6 py-3 font-medium transition-colors"
          data-testid="welcome-demo-btn"
        >
          <PlayCircle className="w-4 h-4" strokeWidth={1.75} />
          Explore Demo Project
        </button>
        <button
          type="button"
          onClick={onCreate}
          className="inline-flex items-center gap-2 bg-white text-charcoal border border-stone-border hover:border-charcoal hover:bg-stone-panel rounded-full px-6 py-3 font-medium transition-colors"
          data-testid="welcome-create-btn"
        >
          <Plus className="w-4 h-4" strokeWidth={1.75} />
          Create Your First Project
        </button>
        <button
          type="button"
          onClick={onLibrary}
          className="inline-flex items-center gap-2 bg-white text-charcoal border border-stone-border hover:border-charcoal hover:bg-stone-panel rounded-full px-6 py-3 font-medium transition-colors"
          data-testid="welcome-library-btn"
        >
          <BookOpen className="w-4 h-4" strokeWidth={1.75} />
          Open Material Library
        </button>
      </div>
      <div className="mt-10 grid sm:grid-cols-2 lg:grid-cols-5 gap-4">
        {WORKFLOW_STEPS.map((s, i) => (
          <div key={s.title} className="p-4 rounded-2xl bg-stone-panel/60 border border-stone-border-soft" data-testid={`welcome-step-${i}`}>
            <div className="flex items-center gap-2 text-warm-grey mb-2">
              <s.icon className="w-4 h-4" strokeWidth={1.5} />
              <span className="text-[10px] font-mono tabular-nums">0{i + 1}</span>
            </div>
            <div className="font-display font-semibold text-sm text-charcoal">{s.title}</div>
            <p className="text-xs text-warm-grey mt-1 leading-relaxed">{s.body}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

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
  const openDemo = () => navigate("/demo");
  const openCreate = () => navigate("/projects/new");
  const openLibrary = () => navigate("/library");

  const deleteProject = async (p, e) => {
    // The project card is a <button>; we're a nested control so we
    // MUST stop propagation so the card's onClick doesn't navigate
    // the user into a project they just asked to delete.
    e?.stopPropagation?.();
    e?.preventDefault?.();
    const ok = window.confirm(
      `Delete project "${p.name}"?\n\nThis permanently removes the project, its reference image, and all specification data. This cannot be undone.`
    );
    if (!ok) return;
    try {
      await api.delete(`/projects/${p.id}`);
      setProjects((cur) => cur.filter((x) => x.id !== p.id));
      toast.success(`Project "${p.name}" deleted`);
    } catch (err) {
      toast.error(formatApiError(err));
    }
  };

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
    return (
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-6">
        {projects.map((p) => (
          <div
            key={p.id}
            className="relative group text-left bg-white border border-stone-border-soft rounded-2xl overflow-hidden shadow-soft hover:shadow-hover hover:-translate-y-1 transition-all duration-300"
            data-testid={`project-card-${p.id}`}
          >
            <button
              type="button"
              onClick={() => navigate(projectRoute(p))}
              className="block w-full text-left"
              data-testid={`project-card-open-${p.id}`}
            >
              <div className="aspect-[16/10] bg-stone-panel relative grain">
                <div className="absolute inset-0 grid place-items-center">
                  <div className="text-overline">{p.name?.slice(0, 2).toUpperCase() || "PR"}</div>
                </div>
                <span className={`absolute top-3 left-3 text-[10px] px-2 py-1 rounded-full font-medium ${statusColor[p.status] || statusColor.draft}`}>
                  {p.status || "draft"}
                </span>
              </div>
              <div className="p-5">
                <h3 className="font-display font-semibold text-base mb-1 truncate text-charcoal">{p.name}</h3>
                <p className="text-xs text-warm-grey truncate">{p.client_name || "No client"}</p>
                <div className="mt-3 flex items-center gap-1 text-xs text-warm-grey/70">
                  <Clock className="w-3 h-3" strokeWidth={1.5} />
                  {new Date(p.created_at).toLocaleDateString()}
                </div>
              </div>
            </button>
            {/* Absolute-positioned delete control — outside the open
                button so clicks don't bubble. Opacity ramps up on
                hover / focus so the card stays clean at rest but the
                affordance is always keyboard-reachable. */}
            <button
              type="button"
              onClick={(e) => deleteProject(p, e)}
              className="absolute top-3 right-3 inline-flex items-center justify-center w-8 h-8 rounded-full bg-white/90 backdrop-blur-sm border border-stone-border text-warm-grey hover:text-red-600 hover:border-red-300 hover:bg-white opacity-0 group-hover:opacity-100 focus:opacity-100 focus:outline-none focus:ring-2 focus:ring-red-200 transition-all"
              title={`Delete project "${p.name}"`}
              aria-label={`Delete project ${p.name}`}
              data-testid={`project-delete-btn-${p.id}`}
            >
              <Trash2 className="w-4 h-4" strokeWidth={1.5} />
            </button>
          </div>
        ))}
      </div>
    );
  };

  const hasProjects = projects.length > 0;

  return (
    <div className="min-h-screen bg-paper" data-testid="dashboard-page">
      <Header />

      <main className="max-w-7xl mx-auto px-6 py-12">
        <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-6 mb-10">
          <div>
            <div className="text-overline mb-2">Welcome back</div>
            <h1 className="font-display text-4xl sm:text-5xl font-bold tracking-tight text-charcoal">
              {user?.name || "Designer"}.
            </h1>
            <p className="text-warm-grey mt-2">Pick up where you left off, or start a new project.</p>
          </div>
          {hasProjects && (
            <div className="flex items-center gap-2 self-start">
              <button
                type="button"
                onClick={openDemo}
                className="inline-flex items-center gap-2 bg-white text-charcoal border border-stone-border hover:border-charcoal rounded-full px-4 py-2.5 text-sm font-medium transition-colors"
                data-testid="dashboard-demo-btn"
              >
                <PlayCircle className="w-4 h-4" strokeWidth={1.75} />
                Explore Demo
              </button>
              <button
                type="button"
                onClick={openCreate}
                className="inline-flex items-center gap-2 bg-charcoal text-paper hover:bg-charcoal/85 rounded-full px-6 py-2.5 font-medium transition-colors"
                data-testid="new-project-btn"
              >
                <Plus className="w-4 h-4" strokeWidth={1.5} />
                New project
              </button>
            </div>
          )}
        </div>

        {/* Empty state welcome panel */}
        {!loading && !hasProjects && (
          <div className="mb-12">
            <WelcomePanel onDemo={openDemo} onCreate={openCreate} onLibrary={openLibrary} />
          </div>
        )}

        {hasProjects && (
          <>
            {/* Stat cards */}
            <div className="grid sm:grid-cols-3 gap-6 mb-12">
              {[
                { label: "Active projects", value: projects.length, icon: Sparkles },
                { label: "Completed reports", value: reports.length, icon: FileText },
                { label: "Sourcing region", value: user?.preferred_region || "IN", icon: ArrowUpRight, sub: "Active search scope" },
              ].map((s, i) => (
                <div key={s.label} className="bg-white border border-stone-border-soft rounded-2xl p-6 shadow-soft" data-testid={`stat-card-${i}`}>
                  <div className="flex items-start justify-between mb-6">
                    <span className="text-overline">{s.label}</span>
                    <s.icon className="w-5 h-5 text-warm-grey" strokeWidth={1.25} />
                  </div>
                  <div className="font-display text-4xl font-bold tracking-tight text-charcoal">{s.value}</div>
                  {s.sub && <div className="text-xs text-warm-grey mt-1">{s.sub}</div>}
                </div>
              ))}
            </div>

            {/* Recent projects */}
            <section className="mb-16">
              <div className="flex items-baseline justify-between mb-6">
                <h2 className="font-display text-2xl font-semibold text-charcoal">Recent projects</h2>
              </div>
              {renderProjectsSection()}
            </section>
          </>
        )}

        {/* Recent reports */}
        {hasProjects && (
          <section>
            <div className="flex items-baseline justify-between mb-6">
              <h2 className="font-display text-2xl font-semibold text-charcoal">Recent reports</h2>
            </div>
            {reports.length === 0 ? (
              <div className="bg-white border border-dashed border-stone-border rounded-2xl p-8 text-center text-sm text-warm-grey" data-testid="empty-reports">
                Reports appear here after you complete an analysis.
              </div>
            ) : (
              <div className="bg-white border border-stone-border-soft rounded-2xl divide-y divide-stone-border-soft shadow-soft">
                {reports.map((r) => (
                  <Link
                    key={r.id}
                    to={`/projects/${r.project_id}/report`}
                    className="flex items-center justify-between gap-4 p-5 hover:bg-stone-panel/60 transition-colors"
                    data-testid={`report-row-${r.id}`}
                  >
                    <div className="flex items-center gap-4">
                      <div className="w-10 h-10 rounded-lg bg-stone-panel grid place-items-center">
                        <FileText className="w-5 h-5 text-charcoal" strokeWidth={1.25} />
                      </div>
                      <div>
                        <div className="font-medium text-sm text-charcoal">{r.project_name}</div>
                        <div className="text-xs text-warm-grey">{r.client_name || "—"} · {new Date(r.created_at).toLocaleDateString()}</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      <div className="text-right hidden sm:block">
                        <div className="text-xs text-warm-grey">Top match</div>
                        <div className="font-display font-semibold text-charcoal">{Math.round((r.top_score || 0) * 100)}%</div>
                      </div>
                      <ArrowUpRight className="w-4 h-4 text-warm-grey" strokeWidth={1.5} />
                    </div>
                  </Link>
                ))}
              </div>
            )}
          </section>
        )}
      </main>
    </div>
  );
}
