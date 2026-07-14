import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import Header from "@/components/Header";
import { useAuth } from "@/context/AuthContext";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import {
  ArrowLeft, Search, Upload as UploadIcon, Trash2, Edit3, Eye, AlertCircle,
} from "lucide-react";

function StatusPill({ text, tone = "muted" }) {
  const cls = {
    ok: "bg-emerald-50 text-emerald-700 border-emerald-200",
    warn: "bg-orange-50 text-orange-700 border-orange-200",
    muted: "bg-stone-panel text-warm-grey border-stone-border-soft",
  }[tone];
  return (
    <span className={`text-[9px] uppercase tracking-widest px-1.5 py-0.5 rounded-full border font-semibold ${cls}`}>
      {text}
    </span>
  );
}

function RecordCard({ r, onPreview, onEdit, onDelete, canManage }) {
  const swatch = r.page_preview_b64;
  return (
    <div className="border border-stone-border-soft rounded-xl p-3 bg-white flex items-start gap-3">
      <div
        className="w-16 h-16 rounded-lg border border-stone-border-soft shrink-0 overflow-hidden"
        style={{ backgroundColor: r.color_hex || "#EDE9DE" }}
      >
        {swatch && (
          <img
            src={`data:image/jpeg;base64,${swatch}`}
            alt={r.material_name}
            className="w-full h-full object-cover"
          />
        )}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="text-xs uppercase tracking-widest text-warm-grey truncate">
              {r.brand || "Unknown brand"}
              {r.collection_name && <> · <span className="italic">{r.collection_name}</span></>}
            </div>
            <div className="font-semibold text-sm text-charcoal truncate" data-testid="category-record-name">
              {r.material_name || "Untitled"}
            </div>
            <div className="text-[11px] text-warm-grey truncate">
              {r.material_code ? `${r.material_code} · ` : ""}
              {r.material_family || r.category}
              {r.finish ? ` · ${r.finish}` : ""}
              {r.page_number ? ` · page ${r.page_number}` : ""}
            </div>
          </div>
          <div className="shrink-0 flex flex-col items-end gap-1">
            {r.needs_review && <StatusPill text="Needs review" tone="warn" />}
            {r.color_hex && (
              <span className="text-[10px] font-mono text-warm-grey">{r.color_hex}</span>
            )}
          </div>
        </div>
        <div className="mt-2 flex items-center gap-1 flex-wrap">
          {r.upload_id && r.page_number && (
            <button
              type="button"
              onClick={() => onPreview(r)}
              className="text-[10px] uppercase tracking-widest text-warm-grey hover:text-charcoal px-2 py-0.5 rounded-full border border-stone-border-soft bg-white inline-flex items-center gap-1"
              data-testid="category-preview"
            >
              <Eye className="w-3 h-3" /> Preview page
            </button>
          )}
          {canManage && (
            <>
              <button
                type="button"
                onClick={() => onEdit(r)}
                className="text-[10px] uppercase tracking-widest text-warm-grey hover:text-charcoal px-2 py-0.5 rounded-full border border-stone-border-soft bg-white inline-flex items-center gap-1"
                data-testid="category-edit"
              >
                <Edit3 className="w-3 h-3" /> Edit
              </button>
              <button
                type="button"
                onClick={() => onDelete(r)}
                className="text-[10px] uppercase tracking-widest text-rose-700 hover:bg-rose-50 px-2 py-0.5 rounded-full border border-rose-200 bg-white inline-flex items-center gap-1"
                data-testid="category-delete"
              >
                <Trash2 className="w-3 h-3" /> Delete
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function PagePreviewModal({ uploadId, page, onClose }) {
  const [img, setImg] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const r = await api.get(`/admin/studio/uploads/${uploadId}/page/${page}`);
        if (active) setImg(r.data.image_b64);
      } catch (e) {
        if (active) setErr(formatApiError(e));
      }
    })();
    return () => { active = false; };
  }, [uploadId, page]);
  return (
    <div className="fixed inset-0 bg-charcoal/70 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl border border-stone-border-soft p-4 max-w-4xl max-h-[90vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <div className="text-overline">Source page {page}</div>
          <button onClick={onClose} className="text-xs px-3 py-1 rounded-full border border-stone-border-soft hover:bg-stone-panel/40">Close</button>
        </div>
        {err && <div className="text-sm text-rose-700 p-4">{err}</div>}
        {!err && !img && <div className="text-sm text-warm-grey p-4">Loading preview…</div>}
        {img && <img src={`data:image/jpeg;base64,${img}`} alt="preview" className="max-w-full h-auto rounded-lg border" />}
      </div>
    </div>
  );
}

export default function MaterialLibraryCategory() {
  const { category } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const catCap = useMemo(() => {
    if (!category) return "";
    return category.charAt(0).toUpperCase() + category.slice(1).toLowerCase();
  }, [category]);
  const canManage = user?.role === "admin";

  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [brand, setBrand] = useState("all");
  const [preview, setPreview] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      if (canManage) {
        const r = await api.get("/admin/studio/library", { params: { category: catCap, limit: 500 } });
        setRecords(r.data.records || []);
      } else {
        // Non-admin viewers see the read-only aggregate library grouped
        // by category. We just filter locally.
        const r = await api.get("/library/global");
        const grouped = r.data.grouped || {};
        setRecords(grouped[catCap] || []);
      }
    } catch (e) {
      toast.error(formatApiError(e));
      setRecords([]);
    } finally {
      setLoading(false);
    }
  }, [catCap, canManage]);

  useEffect(() => { load(); }, [load]);

  const brands = useMemo(() => {
    const s = new Set();
    records.forEach((r) => { if (r.brand) s.add(r.brand); });
    return ["all", ...Array.from(s).sort()];
  }, [records]);

  const filtered = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return records.filter((r) => {
      if (brand !== "all" && r.brand !== brand) return false;
      if (!ql) return true;
      const hay = `${r.material_name || ""} ${r.material_code || ""} ${r.brand || ""} ${r.collection_name || ""} ${r.material_family || ""}`.toLowerCase();
      return hay.includes(ql);
    });
  }, [records, q, brand]);

  const needsReviewCount = useMemo(
    () => records.filter((r) => r.needs_review).length,
    [records],
  );

  const onEdit = () => toast.info("Open the Review Queue in Studio to edit this record.");
  const onDelete = async (r) => {
    if (!window.confirm(`Delete "${r.material_name}"? This removes it from the Knowledge Engine.`)) return;
    try {
      await api.delete(`/admin/studio/records/${r.id}`);
      toast.success("Record deleted");
      load();
    } catch (e) {
      toast.error(formatApiError(e));
    }
  };
  const onUpload = () => {
    // Pre-select this category in the Studio upload flow (F — hint only).
    navigate(`/admin/studio?tab=upload&category=${encodeURIComponent(catCap)}`);
  };

  return (
    <div className="min-h-screen bg-paper" data-testid="library-category-page">
      <Header />
      <main className="max-w-6xl mx-auto px-6 py-10">
        <button
          type="button"
          onClick={() => navigate("/library")}
          className="text-xs uppercase tracking-widest text-warm-grey hover:text-charcoal inline-flex items-center gap-1 mb-4"
          data-testid="library-back"
        >
          <ArrowLeft className="w-3 h-3" /> Back to all libraries
        </button>

        <div className="flex items-start justify-between gap-4 flex-wrap mb-6">
          <div>
            <div className="text-overline mb-1">Material Library</div>
            <h1 className="font-display text-3xl font-semibold text-charcoal">{catCap} Library</h1>
            <p className="text-warm-grey text-sm mt-1">
              {loading ? "Loading…" : `${records.length} published record${records.length === 1 ? "" : "s"}`}
              {needsReviewCount > 0 && (
                <> · <span className="text-orange-700 inline-flex items-center gap-1"><AlertCircle className="w-3 h-3" /> {needsReviewCount} need review</span></>
              )}
            </p>
          </div>
          {canManage && (
            <button
              type="button"
              onClick={onUpload}
              className="inline-flex items-center gap-2 text-sm bg-charcoal text-white px-4 py-2 rounded-full hover:bg-charcoal/90"
              data-testid="library-upload-catalogue"
            >
              <UploadIcon className="w-4 h-4" /> Upload {catCap} catalogue
            </button>
          )}
        </div>

        {/* Search + filter */}
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <div className="inline-flex items-center gap-2 border border-stone-border-soft rounded-full px-3 py-1.5 bg-white flex-1 min-w-[240px] max-w-md">
            <Search className="w-3.5 h-3.5 text-warm-grey" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={`Search ${catCap.toLowerCase()} records`}
              className="bg-transparent outline-none text-sm flex-1"
              data-testid="library-search"
            />
          </div>
          <select
            value={brand}
            onChange={(e) => setBrand(e.target.value)}
            className="text-xs border border-stone-border-soft rounded-full px-3 py-1.5 bg-white"
            data-testid="library-brand-filter"
          >
            {brands.map((b) => (
              <option key={b} value={b}>{b === "all" ? "All brands" : b}</option>
            ))}
          </select>
        </div>

        {loading ? (
          <div className="text-center text-warm-grey text-sm py-16">Loading library…</div>
        ) : filtered.length === 0 ? (
          <div className="text-center border border-dashed border-stone-border-soft rounded-2xl py-16">
            <div className="font-display text-base font-semibold text-charcoal mb-1">
              No {catCap.toLowerCase()} records yet
            </div>
            <p className="text-warm-grey text-sm mb-4">
              {records.length === 0
                ? `Upload a supplier catalogue to start building the ${catCap} library.`
                : "No records match your search."}
            </p>
            {canManage && records.length === 0 && (
              <button
                type="button"
                onClick={onUpload}
                className="inline-flex items-center gap-2 text-sm bg-charcoal text-white px-4 py-2 rounded-full hover:bg-charcoal/90"
                data-testid="library-empty-upload"
              >
                <UploadIcon className="w-4 h-4" /> Upload catalogue
              </button>
            )}
          </div>
        ) : (
          <div className="grid md:grid-cols-2 gap-3" data-testid="library-record-list">
            {filtered.map((r) => (
              <RecordCard
                key={r.id}
                r={r}
                canManage={canManage}
                onPreview={(rr) => setPreview({ upload_id: rr.upload_id, page: rr.page_number })}
                onEdit={onEdit}
                onDelete={onDelete}
              />
            ))}
          </div>
        )}

        {preview && (
          <PagePreviewModal
            uploadId={preview.upload_id}
            page={preview.page}
            onClose={() => setPreview(null)}
          />
        )}
      </main>
    </div>
  );
}
