import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import Header from "@/components/Header";
import { useAuth } from "@/context/AuthContext";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { Search, Filter, Database, Lock, Upload, FileText, Rocket, ArrowRight } from "lucide-react";

function StatBox({ label, value, testid }) {
  return (
    <div className="rounded-xl border border-stone-border-soft bg-white p-4" data-testid={testid}>
      <div className="text-[10px] uppercase tracking-widest text-warm-grey/70 mb-1">{label}</div>
      <div className="font-display text-2xl font-bold text-charcoal">{value}</div>
    </div>
  );
}

function RecordRow({ r, index }) {
  return (
    <div
      className="flex items-start gap-3 border border-stone-border-soft rounded-xl bg-white p-3 hover:border-charcoal/30 transition-colors"
      data-testid={`ke-record-${index}`}
    >
      <div
        className="w-12 h-12 rounded-lg shrink-0 border border-stone-border-soft shadow-inner"
        style={{ backgroundColor: r.color_hex || "#B7ADA0" }}
        title={r.color_name || ""}
      />
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline justify-between gap-2">
          <div className="min-w-0">
            <div className="text-[10px] uppercase tracking-widest text-warm-grey/70 truncate">
              {r.brand} · {r.catalogue}
            </div>
            <div className="text-sm font-semibold text-charcoal truncate">{r.material_name}</div>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {r.source === "Uploaded PDF" && (
              <span
                className="text-[9px] uppercase tracking-widest px-1.5 py-0.5 rounded-full border font-semibold bg-charcoal text-white border-charcoal"
                data-testid={`ke-record-source-uploaded-${index}`}
              >
                Uploaded
              </span>
            )}
            {r.source === "Reference catalogue" && (
              <span
                className="text-[9px] uppercase tracking-widest px-1.5 py-0.5 rounded-full border font-semibold bg-stone-panel text-warm-grey border-stone-border-soft"
                data-testid={`ke-record-source-reference-${index}`}
              >
                Reference
              </span>
            )}
            <span className="text-[9px] uppercase tracking-widest px-1.5 py-0.5 rounded-full border font-semibold bg-sage-soft text-sage border-sage/30">
              {r.status || "published"}
            </span>
          </div>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-warm-grey">
          <span>Cat · <span className="text-charcoal">{r.category}</span></span>
          <span>Family · <span className="text-charcoal">{r.material_family}</span></span>
          {r.material_code && <span>Code · <span className="font-mono text-charcoal">{r.material_code}</span></span>}
          {r.page_number && <span>Page · <span className="font-mono text-charcoal">{r.page_number}</span></span>}
          {r.finish && <span>Finish · <span className="text-charcoal">{r.finish}</span></span>}
        </div>
      </div>
    </div>
  );
}

export default function AdminKnowledgeEngine() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [meta, setMeta] = useState(null);
  const [records, setRecords] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [brand, setBrand] = useState("");

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (category) params.set("category", category);
      if (brand) params.set("brand", brand);
      params.set("limit", "80");
      const r = await api.get(`/admin/knowledge-engine?${params.toString()}`);
      setMeta(r.data);
      setRecords(r.data.records || []);
      setTotal(r.data.total || 0);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [q, category, brand]);

  useEffect(() => {
    if (!user) return;
    if (user.role !== "admin") { navigate("/dashboard"); return; }
    load();
  }, [user, load, navigate]);

  const categories = meta?.filter_meta?.categories || [];
  const brands = meta?.filter_meta?.brands || [];
  const catCounts = useMemo(() => {
    // Approximate live count by counting displayed records grouped by category.
    // The API returns page-1; for a real dashboard the endpoint could return counts too.
    return null;
  }, []);

  if (!user || user.role !== "admin") {
    return (
      <div className="min-h-screen bg-paper" data-testid="ke-page-admin-only">
        <Header />
        <main className="max-w-6xl mx-auto px-6 py-24 text-center">
          <Lock className="w-8 h-8 text-warm-grey mx-auto mb-3" strokeWidth={1.5} />
          <h1 className="font-display text-2xl font-semibold text-charcoal mb-1">Admin access required</h1>
          <p className="text-warm-grey text-sm">This page manages the MaterialMatch Library and is restricted to admin users.</p>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-paper" data-testid="admin-knowledge-engine-page">
      <Header />
      <main className="max-w-6xl mx-auto px-6 py-12">
        <div className="mb-8">
          <div className="text-overline mb-2 flex items-center gap-2">
            <Database className="w-3.5 h-3.5" strokeWidth={1.75} />
            Admin · Knowledge Engine
          </div>
          <h1 className="font-display text-4xl font-bold tracking-tight text-charcoal">MaterialMatch Library</h1>
          <p className="text-warm-grey mt-2 max-w-2xl">
            Read-only browse of the platform-managed material & product Knowledge Engine. Uploaded catalogue records (via MaterialMatch Studio) are ranked ahead of the seeded library.
          </p>
          <div className="mt-4">
            <button
              type="button"
              onClick={() => navigate("/admin/studio")}
              className="inline-flex items-center gap-2 text-sm bg-charcoal text-white px-4 py-2 rounded-full font-medium hover:bg-charcoal/90"
              data-testid="ke-open-studio"
            >
              <Rocket className="w-4 h-4" strokeWidth={1.5} />
              Open MaterialMatch Studio
              <ArrowRight className="w-3.5 h-3.5" strokeWidth={1.75} />
            </button>
          </div>
        </div>

        {/* Coverage stats */}
        <div className="grid sm:grid-cols-3 gap-3 mb-6" data-testid="ke-coverage">
          <StatBox label="Records indexed" value={total} testid="ke-stat-total" />
          <StatBox label="Categories" value={categories.length} testid="ke-stat-categories" />
          <StatBox label="Brands" value={brands.length} testid="ke-stat-brands" />
        </div>

        {/* Filters */}
        <div className="bg-white border border-stone-border-soft rounded-2xl p-4 mb-6 flex flex-wrap items-center gap-2" data-testid="ke-filters">
          <div className="flex-1 min-w-[240px] relative">
            <Search className="w-3.5 h-3.5 text-warm-grey absolute left-3 top-1/2 -translate-y-1/2" strokeWidth={1.75} />
            <input
              type="text"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search brand, material name, keyword…"
              className="w-full pl-9 pr-3 py-2 text-sm rounded-full border border-stone-border-soft bg-paper focus:outline-none focus:border-charcoal"
              data-testid="ke-search"
            />
          </div>
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="text-xs px-3 py-2 rounded-full border border-stone-border-soft bg-white text-charcoal"
            data-testid="ke-category-filter"
          >
            <option value="">All categories</option>
            {categories.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <select
            value={brand}
            onChange={(e) => setBrand(e.target.value)}
            className="text-xs px-3 py-2 rounded-full border border-stone-border-soft bg-white text-charcoal"
            data-testid="ke-brand-filter"
          >
            <option value="">All brands</option>
            {brands.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
          {(q || category || brand) && (
            <button
              type="button"
              onClick={() => { setQ(""); setCategory(""); setBrand(""); }}
              className="text-xs px-3 py-2 text-warm-grey hover:text-charcoal"
              data-testid="ke-reset-filters"
            >
              Reset
            </button>
          )}
        </div>

        {/* Records */}
        {loading ? (
          <div className="text-center text-sm text-warm-grey py-16">Loading knowledge engine…</div>
        ) : records.length === 0 ? (
          <div className="text-center text-sm text-warm-grey py-16 border border-dashed border-stone-border rounded-2xl" data-testid="ke-empty">
            No records match the current filters.
          </div>
        ) : (
          <div className="grid md:grid-cols-2 gap-3" data-testid="ke-records">
            {records.map((r, i) => <RecordRow key={r.id} r={r} index={i} />)}
          </div>
        )}

        {/* Upcoming section — data-model-ready placeholders */}
        <div className="mt-10 grid sm:grid-cols-3 gap-3" data-testid="ke-upcoming">
          <div className="rounded-2xl border border-dashed border-stone-border p-5 bg-stone-panel/40">
            <div className="flex items-center gap-2 mb-2">
              <FileText className="w-4 h-4 text-warm-grey" strokeWidth={1.5} />
              <div className="text-overline">Manual record entry</div>
            </div>
            <p className="text-xs text-warm-grey">Add / edit / hide records via a form. Data model ready — coming Sprint 3.5.</p>
          </div>
          <div className="rounded-2xl border border-dashed border-stone-border p-5 bg-stone-panel/40">
            <div className="flex items-center gap-2 mb-2">
              <Filter className="w-4 h-4 text-warm-grey" strokeWidth={1.5} />
              <div className="text-overline">CSV / JSON import</div>
            </div>
            <p className="text-xs text-warm-grey">Bulk-import supplier catalogues. Coming Sprint 3.5.</p>
          </div>
          <div className="rounded-2xl border border-dashed border-stone-border p-5 bg-stone-panel/40">
            <div className="flex items-center gap-2 mb-2">
              <Upload className="w-4 h-4 text-warm-grey" strokeWidth={1.5} />
              <div className="text-overline">PDF ingestion</div>
            </div>
            <p className="text-xs text-warm-grey">Processing pipeline coming next. Uploads will parse into indexable records automatically.</p>
          </div>
        </div>
      </main>
    </div>
  );
}
