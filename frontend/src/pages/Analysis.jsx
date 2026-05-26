import React, { useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import Header from "@/components/Header";
import api, { formatApiError } from "@/lib/api";
import { ArrowLeft, FileDown, Sparkles, Layers } from "lucide-react";
import { toast } from "sonner";

function scoreBadgeColor(score) {
  if (score >= 0.8) return "bg-emerald-600";
  if (score >= 0.6) return "bg-emerald-500";
  if (score >= 0.4) return "bg-amber-500";
  return "bg-neutral-400";
}

export default function Analysis() {
  const { id } = useParams();
  const [project, setProject] = useState(null);
  const [refImg, setRefImg] = useState(null);
  const [catImages, setCatImages] = useState({}); // idx -> data_url
  const [status, setStatus] = useState("loading");
  const pollRef = useRef(null);

  const fetchProject = async () => {
    try {
      const { data } = await api.get(`/projects/${id}`);
      setProject(data);
      setStatus(data.status || "draft");
      return data;
    } catch (e) {
      toast.error(formatApiError(e));
      setStatus("error");
      return null;
    }
  };

  const loadRefImg = async () => {
    try {
      const { data } = await api.get(`/projects/${id}/reference-image`);
      setRefImg(data.data_url);
    } catch (e) { /* ignore */ }
  };

  useEffect(() => {
    fetchProject();
    loadRefImg();
  }, [id]);

  useEffect(() => {
    if (status === "queued" || status === "analyzing") {
      pollRef.current = setInterval(async () => {
        const data = await fetchProject();
        if (data && (data.status === "completed" || data.status === "error")) {
          clearInterval(pollRef.current);
        }
      }, 3000);
      return () => clearInterval(pollRef.current);
    }
  }, [status]);

  const loadCatImg = async (idx) => {
    if (catImages[idx]) return;
    try {
      const { data } = await api.get(`/projects/${id}/catalogue/${idx}`);
      setCatImages((prev) => ({ ...prev, [idx]: data.data_url }));
    } catch (e) { /* ignore */ }
  };

  useEffect(() => {
    if (project?.analysis?.matches) {
      project.analysis.matches.slice(0, 12).forEach((m) => loadCatImg(m.index));
    }
    // eslint-disable-next-line
  }, [project?.analysis]);

  const analysis = project?.analysis;
  const isWorking = status === "queued" || status === "analyzing";

  return (
    <div className="min-h-screen bg-[#F9F9F8]" data-testid="analysis-page">
      <Header />
      <main className="max-w-7xl mx-auto px-6 py-12">
        <div className="flex items-center justify-between mb-8 flex-wrap gap-4">
          <Link to="/dashboard" className="inline-flex items-center gap-2 text-sm text-neutral-600 hover:text-black" data-testid="back-to-dashboard">
            <ArrowLeft className="w-4 h-4" strokeWidth={1.5} />
            Back to dashboard
          </Link>
          {status === "completed" && (
            <Link
              to={`/projects/${id}/report`}
              className="inline-flex items-center gap-2 bg-black text-white hover:bg-black/80 rounded-full px-6 py-3 text-sm font-medium transition-colors"
              data-testid="view-report-btn"
            >
              <FileDown className="w-4 h-4" strokeWidth={1.5} />
              View report
            </Link>
          )}
        </div>

        <div className="mb-10">
          <div className="text-overline mb-2">Analysis · {project?.name || "—"}</div>
          <h1 className="font-display text-4xl sm:text-5xl font-bold tracking-tight">Material matches.</h1>
        </div>

        {isWorking && (
          <div className="bg-white border border-black/5 rounded-2xl p-12 text-center shadow-soft" data-testid="analysis-working">
            <div className="inline-flex items-center gap-3 mb-4">
              <Sparkles className="w-6 h-6 text-neutral-900 animate-pulse" strokeWidth={1.25} />
              <span className="text-overline">{status === "queued" ? "Queued" : "Analyzing"}</span>
            </div>
            <h2 className="font-display text-2xl font-semibold mb-2">Claude is reading the materials.</h2>
            <p className="text-neutral-500 max-w-md mx-auto mb-8">This typically takes 30 seconds to 2 minutes depending on catalogue size. You can leave this page — the analysis runs in the background.</p>
            <div className="max-w-md mx-auto h-2 rounded-full bg-[#F3F2EE] overflow-hidden">
              <div className="h-full bg-black animate-pulse" style={{ width: "60%" }}></div>
            </div>
          </div>
        )}

        {status === "error" && (
          <div className="bg-red-50 border border-red-100 rounded-2xl p-8" data-testid="analysis-error">
            <h2 className="font-display font-semibold text-red-900 mb-2">Analysis failed</h2>
            <p className="text-sm text-red-700">{project?.analysis_error || "Unknown error"}</p>
            <Link to={`/projects/${id}/upload`} className="inline-block mt-4 text-sm underline text-red-900">Try again</Link>
          </div>
        )}

        {status === "completed" && analysis && (
          <div className="grid lg:grid-cols-12 gap-8">
            {/* Left sticky panel */}
            <aside className="lg:col-span-4">
              <div className="lg:sticky lg:top-24 space-y-6">
                <div className="bg-white border border-black/5 rounded-2xl overflow-hidden shadow-soft" data-testid="reference-card">
                  {refImg ? (
                    <img src={refImg} alt="Reference" className="w-full aspect-[4/5] object-cover" />
                  ) : (
                    <div className="w-full aspect-[4/5] shimmer"></div>
                  )}
                  <div className="p-5">
                    <div className="text-overline mb-2">Reference</div>
                    <p className="text-sm text-neutral-700 leading-relaxed">{analysis.summary}</p>
                    {analysis.style_tags?.length > 0 && (
                      <div className="flex flex-wrap gap-2 mt-4">
                        {analysis.style_tags.map((t, i) => (
                          <span key={i} className="inline-flex px-2.5 py-1 rounded-full text-xs bg-[#F3F2EE] text-neutral-700">{t}</span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                {analysis.color_palette?.length > 0 && (
                  <div className="bg-white border border-black/5 rounded-2xl p-5 shadow-soft" data-testid="color-palette">
                    <div className="text-overline mb-3">Color palette</div>
                    <div className="grid grid-cols-3 gap-3">
                      {analysis.color_palette.map((c, i) => (
                        <div key={i} className="space-y-1.5">
                          <div className="aspect-square rounded-lg border border-black/5" style={{ background: c.hex }}></div>
                          <div className="text-[10px] text-neutral-600 truncate">{c.name}</div>
                          <div className="text-[10px] text-neutral-400 font-mono">{c.hex}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {analysis.materials?.length > 0 && (
                  <div className="bg-white border border-black/5 rounded-2xl p-5 shadow-soft" data-testid="detected-materials">
                    <div className="text-overline mb-3">Detected materials</div>
                    <div className="space-y-3">
                      {analysis.materials.map((m, i) => (
                        <div key={i} className="flex items-start gap-3 pb-3 last:pb-0 border-b last:border-0 border-black/5">
                          <Layers className="w-4 h-4 text-neutral-400 mt-0.5" strokeWidth={1.5} />
                          <div className="flex-1 min-w-0">
                            <div className="text-sm font-medium truncate">{m.name}</div>
                            <div className="text-xs text-neutral-500">{m.category} · {m.finish}</div>
                            {m.location && <div className="text-xs text-neutral-400">{m.location}</div>}
                          </div>
                          <span className="text-xs font-mono text-neutral-500">{Math.round((m.confidence || 0) * 100)}%</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </aside>

            {/* Right matches */}
            <section className="lg:col-span-8">
              <div className="flex items-baseline justify-between mb-6">
                <h2 className="font-display text-2xl font-semibold">Top matches</h2>
                <span className="text-sm text-neutral-500">{analysis.matches?.length || 0} compared</span>
              </div>

              <div className="grid sm:grid-cols-2 gap-6" data-testid="matches-grid">
                {(analysis.matches || []).map((m, i) => (
                  <div
                    key={i}
                    className="bg-white border border-black/5 rounded-2xl overflow-hidden shadow-soft hover:shadow-hover transition-all duration-300 hover:-translate-y-1"
                    data-testid={`match-card-${i}`}
                  >
                    <div className="relative aspect-[4/3] bg-[#F3F2EE] overflow-hidden">
                      {catImages[m.index] ? (
                        <img src={catImages[m.index]} alt={m.name} className="w-full h-full object-cover" />
                      ) : (
                        <div className="w-full h-full shimmer"></div>
                      )}
                      <div className={`absolute top-3 right-3 ${scoreBadgeColor(m.score)} text-white text-xs font-semibold px-2.5 py-1 rounded-full`}>
                        {Math.round((m.score || 0) * 100)}%
                      </div>
                    </div>
                    <div className="p-5 space-y-3">
                      <div>
                        <h3 className="font-display font-semibold truncate">{m.name}</h3>
                        {m.matched_material && (
                          <div className="text-xs text-neutral-500 mt-0.5">Matches: {m.matched_material}</div>
                        )}
                      </div>
                      <p className="text-sm text-neutral-600 leading-relaxed">{m.explanation}</p>
                      {m.tags?.length > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                          {m.tags.map((t, j) => (
                            <span key={j} className="text-[10px] px-2 py-0.5 rounded-full bg-[#F3F2EE] text-neutral-600">{t}</span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </div>
        )}
      </main>
    </div>
  );
}
