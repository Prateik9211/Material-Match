import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import Header from "@/components/Header";
import { useAuth } from "@/context/AuthContext";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import {
  Upload,
  FileText,
  ClipboardList,
  BookOpen,
  Check,
  X,
  Loader2,
  Lock,
  RefreshCw,
  Rocket,
  ChevronRight,
} from "lucide-react";

const TABS = [
  { id: "upload", label: "Upload Catalogue", icon: Upload },
  { id: "processing", label: "Processing Queue", icon: Loader2 },
  { id: "review", label: "Review Queue", icon: ClipboardList },
  { id: "library", label: "Published Library", icon: BookOpen },
];

function StatusBadge({ status }) {
  const map = {
    processing: "bg-amber-50 text-amber-700 border-amber-200",
    review: "bg-blue-50 text-blue-700 border-blue-200",
    published: "bg-emerald-50 text-emerald-700 border-emerald-200",
    failed: "bg-rose-50 text-rose-700 border-rose-200",
    draft: "bg-stone-panel text-warm-grey border-stone-border-soft",
    rejected: "bg-neutral-100 text-neutral-500 border-neutral-200",
  };
  return (
    <span
      className={`text-[9px] uppercase tracking-widest px-1.5 py-0.5 rounded-full border font-semibold ${map[status] || map.draft}`}
      data-testid={`studio-status-${status}`}
    >
      {status}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*                        Tab 1 — Upload Catalogue                    */
/* ------------------------------------------------------------------ */
function UploadTab({ onUploaded }) {
  const fileRef = useRef(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [lastResult, setLastResult] = useState(null);

  const send = async (file) => {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      toast.error("Only PDF catalogues are accepted");
      return;
    }
    if (file.size > 150 * 1024 * 1024) {
      toast.error("PDF is larger than 150 MB");
      return;
    }
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await api.post("/admin/studio/upload", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setLastResult(r.data);
      toast.success(`Extracted ${r.data.records_extracted} record(s) from ${r.data.filename}`);
      onUploaded && onUploaded(r.data);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="space-y-6" data-testid="studio-upload-tab">
      <div
        className={`rounded-2xl border-2 border-dashed p-10 text-center transition-colors ${
          dragOver ? "border-charcoal bg-stone-panel" : "border-stone-border bg-white"
        }`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          send(e.dataTransfer.files?.[0]);
        }}
        data-testid="studio-upload-dropzone"
      >
        <div className="w-12 h-12 rounded-full bg-stone-panel border border-stone-border-soft grid place-items-center mx-auto mb-3">
          <Upload className="w-5 h-5 text-charcoal" strokeWidth={1.5} />
        </div>
        <div className="font-display text-xl font-semibold text-charcoal mb-1">
          Drop a supplier PDF catalogue
        </div>
        <p className="text-sm text-warm-grey mb-5">
          MaterialMatch will parse each page, extract material name, code, category and dominant
          swatch, and place the records into the Review Queue.
        </p>
        <input
          ref={fileRef}
          type="file"
          accept="application/pdf"
          className="hidden"
          onChange={(e) => send(e.target.files?.[0])}
          data-testid="studio-upload-input"
        />
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          className="inline-flex items-center gap-2 bg-charcoal text-white text-sm px-5 py-2.5 rounded-full font-medium hover:bg-charcoal/90 disabled:opacity-50"
          data-testid="studio-upload-btn"
        >
          {uploading ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" /> Extracting…
            </>
          ) : (
            <>
              <Upload className="w-4 h-4" /> Choose PDF
            </>
          )}
        </button>
        <div className="text-[11px] text-warm-grey/80 mt-3">PDF only · max 150 MB · scanned PDFs auto-OCR</div>
      </div>

      {lastResult && (
        <div
          className={`rounded-2xl border p-5 ${
            lastResult.status === "failed"
              ? "border-amber-300 bg-amber-50/70"
              : "border-emerald-200 bg-emerald-50/70"
          }`}
          data-testid="studio-upload-success"
        >
          <div className="flex items-start justify-between gap-4">
            <div>
              <div
                className={`text-[10px] uppercase tracking-widest mb-1 ${
                  lastResult.status === "failed" ? "text-amber-700" : "text-emerald-700"
                }`}
              >
                {lastResult.status === "failed" ? "Ingestion did not extract records" : "Extraction complete"}
              </div>
              <div className="font-display text-lg font-semibold text-charcoal">
                {lastResult.filename}
              </div>
              <p className="text-sm text-warm-grey mt-1">
                {lastResult.records_extracted > 0 ? (
                  <>
                    {lastResult.records_extracted} record(s) extracted across {lastResult.page_count} page(s)
                    {lastResult.extraction_mode === "ocr" && " via OCR"}
                    {lastResult.extraction_mode === "text+ocr" && " (text + OCR fallback)"}
                    . Head to the Review Queue to approve or publish them.
                  </>
                ) : (
                  <>
                    {lastResult.failure_reason || "No records were extracted from this PDF."}
                    {" "}Approve and Publish are disabled until records can be extracted.
                  </>
                )}
              </p>
            </div>
            <StatusBadge status={lastResult.status} />
          </div>
        </div>
      )}

      <div className="rounded-2xl border border-stone-border-soft bg-white p-5">
        <div className="text-overline mb-2">How ingestion works</div>
        <ol className="text-sm text-warm-grey space-y-1.5 list-decimal pl-5">
          <li>PDF is parsed page-by-page (PyMuPDF).</li>
          <li>Material name, product code and category are inferred from text heuristics.</li>
          <li>Scanned / image-based pages automatically fall back to on-server OCR (tesseract).</li>
          <li>Dominant swatch colour is sampled from the page render.</li>
          <li>All records land as <span className="text-charcoal font-semibold">draft</span> in the Review Queue.</li>
          <li>Approve or publish to make them searchable in the live MaterialMatch Library.</li>
        </ol>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*                    Tab 2 — Processing Queue (uploads list)         */
/* ------------------------------------------------------------------ */
function ProcessingTab({ uploads, loading, onRefresh, onOpenReview }) {
  return (
    <div className="space-y-4" data-testid="studio-processing-tab">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-overline mb-1">Ingestion pipeline</div>
          <h2 className="font-display text-2xl font-semibold text-charcoal">Processing Queue</h2>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border border-stone-border-soft bg-white hover:border-charcoal/40"
          data-testid="studio-processing-refresh"
        >
          <RefreshCw className="w-3.5 h-3.5" /> Refresh
        </button>
      </div>
      {loading ? (
        <div className="text-center text-sm text-warm-grey py-12">Loading uploads…</div>
      ) : uploads.length === 0 ? (
        <div
          className="text-center text-sm text-warm-grey py-16 border border-dashed border-stone-border rounded-2xl"
          data-testid="studio-processing-empty"
        >
          No catalogue uploads yet. Head to <span className="text-charcoal font-semibold">Upload Catalogue</span> to ingest your first PDF.
        </div>
      ) : (
        <div className="grid md:grid-cols-2 gap-3" data-testid="studio-uploads-list">
          {uploads.map((u, i) => (
            <button
              key={u.id}
              type="button"
              onClick={() => onOpenReview(u.id)}
              className="text-left rounded-xl border border-stone-border-soft bg-white p-4 hover:border-charcoal/40 transition-colors flex items-start justify-between gap-3"
              data-testid={`studio-upload-row-${i}`}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <FileText className="w-4 h-4 text-warm-grey shrink-0" strokeWidth={1.5} />
                  <div className="text-sm font-semibold text-charcoal truncate" title={u.filename}>
                    {u.filename}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-warm-grey">
                  <span>
                    Pages · <span className="font-mono text-charcoal">{u.page_count ?? "—"}</span>
                  </span>
                  <span>
                    Records ·{" "}
                    <span className="font-mono text-charcoal">{u.records_extracted ?? 0}</span>
                  </span>
                  {u.extraction_mode === "ocr" && (
                    <span className="text-charcoal font-medium">OCR</span>
                  )}
                  {u.extraction_mode === "text+ocr" && (
                    <span className="text-charcoal font-medium">Text + OCR</span>
                  )}
                  <span className="truncate">
                    Uploaded · {new Date(u.created_at).toLocaleString()}
                  </span>
                </div>
                {u.failure_reason && (
                  <div
                    className="mt-2 text-[11px] text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-2 py-1"
                    data-testid={`studio-upload-failure-${i}`}
                  >
                    {u.failure_reason}
                  </div>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <StatusBadge status={u.status} />
                <ChevronRight className="w-4 h-4 text-warm-grey" strokeWidth={1.5} />
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*                     Tab 3 — Review Queue (per upload)              */
/* ------------------------------------------------------------------ */
function ReviewTab({ uploads, selectedUploadId, setSelectedUploadId }) {
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [acting, setActing] = useState(false);

  const load = useCallback(async (uploadId) => {
    if (!uploadId) {
      setRecords([]);
      return;
    }
    setLoading(true);
    try {
      const r = await api.get(`/admin/studio/uploads/${uploadId}/records`);
      setRecords(r.data.records || []);
      setSelected(new Set());
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(selectedUploadId);
  }, [selectedUploadId, load]);

  const toggle = (id) => {
    setSelected((prev) => {
      const s = new Set(prev);
      if (s.has(id)) s.delete(id);
      else s.add(id);
      return s;
    });
  };
  const selectAllDraft = () => {
    setSelected(new Set(records.filter((r) => r.status === "draft").map((r) => r.id)));
  };

  const runAction = async (kind) => {
    const ids = Array.from(selected);
    if (ids.length === 0) {
      toast.info("Select at least one record");
      return;
    }
    setActing(true);
    try {
      const path = kind === "approve" ? "/admin/studio/records/approve" : "/admin/studio/records/reject";
      const r = await api.post(path, { record_ids: ids });
      toast.success(`${kind === "approve" ? "Approved" : "Rejected"} ${r.data[kind + "d"] || ids.length} record(s)`);
      await load(selectedUploadId);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setActing(false);
    }
  };

  const publishAll = async () => {
    if (!selectedUploadId) return;
    setActing(true);
    try {
      const r = await api.post(`/admin/studio/uploads/${selectedUploadId}/publish`);
      toast.success(`Published ${r.data.approved} remaining draft record(s)`);
      await load(selectedUploadId);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setActing(false);
    }
  };

  const activeUpload = uploads.find((u) => u.id === selectedUploadId);
  const draftCount = records.filter((r) => r.status === "draft").length;

  return (
    <div className="space-y-4" data-testid="studio-review-tab">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <div className="text-overline mb-1">Human-in-the-loop</div>
          <h2 className="font-display text-2xl font-semibold text-charcoal">Review Queue</h2>
        </div>
        <select
          value={selectedUploadId || ""}
          onChange={(e) => setSelectedUploadId(e.target.value)}
          className="text-xs px-3 py-2 rounded-full border border-stone-border-soft bg-white text-charcoal min-w-[280px]"
          data-testid="studio-review-upload-select"
        >
          <option value="">Select an upload to review…</option>
          {uploads.map((u) => (
            <option key={u.id} value={u.id}>
              {u.filename} · {u.records_extracted ?? 0} record(s)
            </option>
          ))}
        </select>
      </div>

      {!selectedUploadId ? (
        <div
          className="text-center text-sm text-warm-grey py-16 border border-dashed border-stone-border rounded-2xl"
          data-testid="studio-review-noupload"
        >
          Pick an upload above to review its extracted records.
        </div>
      ) : (
        <>
          <div className="flex items-center justify-between flex-wrap gap-2 bg-white border border-stone-border-soft rounded-2xl p-3">
            <div className="text-xs text-warm-grey">
              {activeUpload && (
                <>
                  <span className="text-charcoal font-semibold">{activeUpload.filename}</span> ·{" "}
                  {records.length} record(s) · {draftCount} pending
                </>
              )}
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={selectAllDraft}
                disabled={draftCount === 0}
                className="text-xs px-3 py-1.5 rounded-full border border-stone-border-soft bg-white hover:border-charcoal/40 disabled:opacity-40"
                data-testid="studio-review-select-all-draft"
              >
                Select all draft
              </button>
              <button
                type="button"
                onClick={() => runAction("reject")}
                disabled={selected.size === 0 || acting}
                className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border border-rose-200 text-rose-700 bg-rose-50 hover:bg-rose-100 disabled:opacity-40"
                data-testid="studio-review-reject"
              >
                <X className="w-3.5 h-3.5" /> Reject ({selected.size})
              </button>
              <button
                type="button"
                onClick={() => runAction("approve")}
                disabled={selected.size === 0 || acting}
                className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full bg-charcoal text-white hover:bg-charcoal/90 disabled:opacity-40"
                data-testid="studio-review-approve"
              >
                <Check className="w-3.5 h-3.5" /> Approve ({selected.size})
              </button>
              <button
                type="button"
                onClick={publishAll}
                disabled={draftCount === 0 || acting}
                className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40"
                data-testid="studio-review-publish-all"
              >
                <Rocket className="w-3.5 h-3.5" /> Publish all drafts
              </button>
            </div>
          </div>

          {loading ? (
            <div className="text-center text-sm text-warm-grey py-12">Loading records…</div>
          ) : records.length === 0 ? (
            <div
              className="text-center text-sm py-14 border border-dashed border-amber-300 bg-amber-50/50 rounded-2xl px-6"
              data-testid="studio-review-empty"
            >
              <div className="font-display text-base font-semibold text-charcoal mb-1">
                No records could be extracted from this upload.
              </div>
              <p className="text-warm-grey text-sm">
                {activeUpload?.failure_reason || "The catalogue layout was not recognised."}
              </p>
              <p className="text-warm-grey text-xs mt-2">
                Approve and Publish are disabled until records can be extracted. Try re-uploading a text-based PDF or a higher-resolution scan.
              </p>
            </div>
          ) : (
            <div className="grid md:grid-cols-2 gap-3" data-testid="studio-review-list">
              {records.map((r, i) => {
                const isSel = selected.has(r.id);
                const disabled = r.status !== "draft";
                return (
                  <label
                    key={r.id}
                    className={`flex items-start gap-3 rounded-xl border p-3 bg-white cursor-pointer transition-colors ${
                      disabled
                        ? "opacity-60 cursor-not-allowed border-stone-border-soft"
                        : isSel
                        ? "border-charcoal/60 bg-stone-panel/40"
                        : "border-stone-border-soft hover:border-charcoal/30"
                    }`}
                    data-testid={`studio-review-row-${i}`}
                  >
                    <input
                      type="checkbox"
                      checked={isSel}
                      disabled={disabled}
                      onChange={() => toggle(r.id)}
                      className="mt-1"
                      data-testid={`studio-review-checkbox-${i}`}
                    />
                    {r.page_preview_b64 ? (
                      <img
                        src={`data:image/jpeg;base64,${r.page_preview_b64}`}
                        alt=""
                        className="w-14 h-14 object-cover rounded-lg border border-stone-border-soft shrink-0"
                      />
                    ) : (
                      <div
                        className="w-14 h-14 rounded-lg shrink-0 border border-stone-border-soft"
                        style={{ backgroundColor: r.color_hex || "#B7ADA0" }}
                      />
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-baseline justify-between gap-2">
                        <div className="min-w-0">
                          <div className="text-[10px] uppercase tracking-widest text-warm-grey/70 truncate">
                            {r.brand || "Unknown brand"} · Page {r.page_number}
                          </div>
                          <div className="text-sm font-semibold text-charcoal truncate">
                            {r.material_name}
                          </div>
                        </div>
                        <StatusBadge status={r.status} />
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-warm-grey">
                        <span>
                          Cat · <span className="text-charcoal">{r.category}</span>
                        </span>
                        {r.material_code && (
                          <span>
                            Code ·{" "}
                            <span className="font-mono text-charcoal">{r.material_code}</span>
                          </span>
                        )}
                        <span>
                          Swatch ·{" "}
                          <span className="font-mono text-charcoal">{r.color_hex}</span>
                        </span>
                      </div>
                    </div>
                  </label>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*                    Tab 4 — Published Library                       */
/* ------------------------------------------------------------------ */
function LibraryTab() {
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [category, setCategory] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (category) params.set("category", category);
      params.set("limit", "200");
      const r = await api.get(`/admin/studio/library?${params.toString()}`);
      setRecords(r.data.records || []);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [category]);

  useEffect(() => {
    load();
  }, [load]);

  const cats = useMemo(() => {
    const s = new Set(records.map((r) => r.category).filter(Boolean));
    return Array.from(s).sort();
  }, [records]);

  return (
    <div className="space-y-4" data-testid="studio-library-tab">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <div className="text-overline mb-1">Live in Knowledge Engine</div>
          <h2 className="font-display text-2xl font-semibold text-charcoal">Published Library</h2>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="text-xs px-3 py-2 rounded-full border border-stone-border-soft bg-white text-charcoal"
            data-testid="studio-library-category"
          >
            <option value="">All categories</option>
            {cats.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={load}
            className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border border-stone-border-soft bg-white hover:border-charcoal/40"
            data-testid="studio-library-refresh"
          >
            <RefreshCw className="w-3.5 h-3.5" /> Refresh
          </button>
        </div>
      </div>

      {loading ? (
        <div className="text-center text-sm text-warm-grey py-12">Loading library…</div>
      ) : records.length === 0 ? (
        <div
          className="text-center text-sm text-warm-grey py-16 border border-dashed border-stone-border rounded-2xl"
          data-testid="studio-library-empty"
        >
          No published records yet. Approve or publish records from the Review Queue.
        </div>
      ) : (
        <div className="grid md:grid-cols-2 gap-3" data-testid="studio-library-list">
          {records.map((r, i) => (
            <div
              key={r.id}
              className="flex items-start gap-3 rounded-xl border border-stone-border-soft bg-white p-3"
              data-testid={`studio-library-row-${i}`}
            >
              {r.page_preview_b64 ? (
                <img
                  src={`data:image/jpeg;base64,${r.page_preview_b64}`}
                  alt=""
                  className="w-14 h-14 object-cover rounded-lg border border-stone-border-soft shrink-0"
                />
              ) : (
                <div
                  className="w-14 h-14 rounded-lg shrink-0 border border-stone-border-soft"
                  style={{ backgroundColor: r.color_hex || "#B7ADA0" }}
                />
              )}
              <div className="flex-1 min-w-0">
                <div className="flex items-baseline justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-[10px] uppercase tracking-widest text-warm-grey/70 truncate">
                      {r.brand || "Uploaded catalogue"} · Page {r.page_number}
                    </div>
                    <div className="text-sm font-semibold text-charcoal truncate">
                      {r.material_name}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    {r.demo_seed && (
                      <span
                        className="text-[9px] uppercase tracking-widest px-1.5 py-0.5 rounded-full border font-semibold bg-stone-panel text-warm-grey border-stone-border-soft"
                        data-testid={`studio-library-reference-${i}`}
                      >
                        Reference
                      </span>
                    )}
                    <StatusBadge status={r.status} />
                  </div>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-warm-grey">
                  <span>
                    Cat · <span className="text-charcoal">{r.category}</span>
                  </span>
                  {r.material_code && (
                    <span>
                      Code ·{" "}
                      <span className="font-mono text-charcoal">{r.material_code}</span>
                    </span>
                  )}
                  <span>
                    Swatch · <span className="font-mono text-charcoal">{r.color_hex}</span>
                  </span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*                            Page shell                              */
/* ------------------------------------------------------------------ */
export default function MaterialMatchStudio() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [tab, setTab] = useState("upload");
  const [uploads, setUploads] = useState([]);
  const [loadingUploads, setLoadingUploads] = useState(true);
  const [selectedUploadId, setSelectedUploadId] = useState("");

  const loadUploads = useCallback(async () => {
    try {
      setLoadingUploads(true);
      const r = await api.get("/admin/studio/uploads");
      setUploads(r.data.uploads || []);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setLoadingUploads(false);
    }
  }, []);

  useEffect(() => {
    if (!user) return;
    if (user.role !== "admin") {
      navigate("/dashboard");
      return;
    }
    loadUploads();
  }, [user, loadUploads, navigate]);

  const totalDraftRecords = useMemo(
    () => uploads.reduce((s, u) => s + (u.status === "review" ? u.records_extracted || 0 : 0), 0),
    [uploads]
  );

  if (!user || user.role !== "admin") {
    return (
      <div className="min-h-screen bg-paper" data-testid="studio-admin-only">
        <Header />
        <main className="max-w-6xl mx-auto px-6 py-24 text-center">
          <Lock className="w-8 h-8 text-warm-grey mx-auto mb-3" strokeWidth={1.5} />
          <h1 className="font-display text-2xl font-semibold text-charcoal mb-1">
            Admin access required
          </h1>
          <p className="text-warm-grey text-sm">
            MaterialMatch Studio is restricted to platform administrators.
          </p>
        </main>
      </div>
    );
  }

  const openReview = (uploadId) => {
    setSelectedUploadId(uploadId);
    setTab("review");
  };

  return (
    <div className="min-h-screen bg-paper" data-testid="material-match-studio-page">
      <Header />
      <main className="max-w-6xl mx-auto px-6 py-12">
        <div className="mb-8">
          <div className="text-overline mb-2 flex items-center gap-2">
            <Rocket className="w-3.5 h-3.5" strokeWidth={1.75} /> Admin · MaterialMatch Studio
          </div>
          <h1 className="font-display text-4xl font-bold tracking-tight text-charcoal">
            Catalogue ingestion workspace
          </h1>
          <p className="text-warm-grey mt-2 max-w-2xl">
            Upload supplier PDFs, review the extracted material records, then publish them so the
            Knowledge Engine matches real catalogues first — ahead of the seeded library.
          </p>
        </div>

        {/* Coverage stats */}
        <div className="grid sm:grid-cols-3 gap-3 mb-6" data-testid="studio-stats">
          <div className="rounded-xl border border-stone-border-soft bg-white p-4">
            <div className="text-[10px] uppercase tracking-widest text-warm-grey/70 mb-1">
              Catalogues ingested
            </div>
            <div className="font-display text-2xl font-bold text-charcoal">{uploads.length}</div>
          </div>
          <div className="rounded-xl border border-stone-border-soft bg-white p-4">
            <div className="text-[10px] uppercase tracking-widest text-warm-grey/70 mb-1">
              Awaiting review
            </div>
            <div className="font-display text-2xl font-bold text-charcoal">{totalDraftRecords}</div>
          </div>
          <div className="rounded-xl border border-stone-border-soft bg-white p-4">
            <div className="text-[10px] uppercase tracking-widest text-warm-grey/70 mb-1">
              Fully published
            </div>
            <div className="font-display text-2xl font-bold text-charcoal">
              {uploads.filter((u) => u.status === "published").length}
            </div>
          </div>
        </div>

        {/* Tabs */}
        <div
          className="flex items-center gap-1 border-b border-stone-border-soft mb-6 overflow-x-auto"
          data-testid="studio-tabs"
        >
          {TABS.map((t) => {
            const Icon = t.icon;
            const active = tab === t.id;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                className={`inline-flex items-center gap-2 px-4 py-3 text-sm border-b-2 transition-colors whitespace-nowrap ${
                  active
                    ? "border-charcoal text-charcoal font-semibold"
                    : "border-transparent text-warm-grey hover:text-charcoal"
                }`}
                data-testid={`studio-tab-${t.id}`}
              >
                <Icon className="w-4 h-4" strokeWidth={1.5} />
                {t.label}
              </button>
            );
          })}
        </div>

        {/* Tab body */}
        {tab === "upload" && (
          <UploadTab
            onUploaded={() => {
              loadUploads();
            }}
          />
        )}
        {tab === "processing" && (
          <ProcessingTab
            uploads={uploads}
            loading={loadingUploads}
            onRefresh={loadUploads}
            onOpenReview={openReview}
          />
        )}
        {tab === "review" && (
          <ReviewTab
            uploads={uploads}
            selectedUploadId={selectedUploadId}
            setSelectedUploadId={setSelectedUploadId}
          />
        )}
        {tab === "library" && <LibraryTab />}
      </main>
    </div>
  );
}
