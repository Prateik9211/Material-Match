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
  Trash2,
  Archive,
  Sparkles,
  AlertCircle,
  Layers,
} from "lucide-react";

const TABS = [
  { id: "upload", label: "Upload Catalogue", icon: Upload },
  // `icon` is the IDLE icon; the processing tab swaps to `Loader2` at
  // render time only while a job is actually running. A permanent
  // `Loader2` used to look like a stuck spinner even when nothing was
  // in flight — see the tab render block below for the swap logic.
  { id: "processing", label: "Processing Queue", icon: Layers },
  { id: "review", label: "Review Queue", icon: ClipboardList },
  { id: "library", label: "Published Library", icon: BookOpen },
];

function StatusBadge({ status, records }) {
  // A `failed` upload with 0 records is meaningfully different from a
  // crash — it means "extraction ran to completion but found nothing".
  // Surface that clearly so admins don't confuse it with a stuck job.
  const effective =
    status === "failed" && (records === 0 || records === undefined)
      ? "no_records"
      : status;
  const map = {
    processing: "bg-amber-50 text-amber-700 border-amber-200",
    review: "bg-blue-50 text-blue-700 border-blue-200",
    review_remaining: "bg-blue-50 text-blue-700 border-blue-200",
    published: "bg-emerald-50 text-emerald-700 border-emerald-200",
    failed: "bg-rose-50 text-rose-700 border-rose-200",
    no_records: "bg-orange-50 text-orange-700 border-orange-200",
    draft: "bg-stone-panel text-warm-grey border-stone-border-soft",
    rejected: "bg-neutral-100 text-neutral-500 border-neutral-200",
    archived: "bg-neutral-100 text-neutral-600 border-neutral-300",
  };
  const label = {
    review_remaining: "review remaining",
    no_records: "needs attention",
  }[effective] || effective;
  return (
    <span
      className={`text-[9px] uppercase tracking-widest px-1.5 py-0.5 rounded-full border font-semibold ${map[effective] || map.draft}`}
      data-testid={`studio-status-${effective}`}
    >
      {label}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*                        Tab 1 — Upload Catalogue                    */
/* ------------------------------------------------------------------ */
function UploadTab({ onUploaded, categoryHint, onClearCategoryHint }) {
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
      if (categoryHint) fd.append("category_hint", categoryHint);
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
      {categoryHint && (
        <div
          className="rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 flex items-center justify-between gap-3"
          data-testid="studio-category-hint"
        >
          <div className="text-sm text-blue-900">
            <span className="font-semibold">Category hint · {categoryHint}</span>
            <span className="ml-2 text-blue-700/80">
              Uploads from this category page will pre-tag the catalogue. The AI still classifies each swatch on its own — the hint never overrides extracted category.
            </span>
          </div>
          <button
            type="button"
            onClick={onClearCategoryHint}
            className="text-[11px] uppercase tracking-widest text-blue-800 hover:text-blue-900 px-2 py-0.5 rounded-full border border-blue-200 bg-white"
            data-testid="studio-category-hint-clear"
          >
            Clear
          </button>
        </div>
      )}
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
function ProcessingTab({ uploads, loading, onRefresh, onOpenReview, onDelete, onArchive, onCleanup, onReprocess, onReplace }) {
  const nonSeedFailed = uploads.filter((u) => !u.demo_seed && u.status === "failed").length;
  return (
    <div className="space-y-4" data-testid="studio-processing-tab">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <div className="text-overline mb-1">Ingestion pipeline</div>
          <h2 className="font-display text-2xl font-semibold text-charcoal">Processing Queue</h2>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onCleanup}
            className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border border-stone-border-soft bg-white hover:border-charcoal/40"
            data-testid="studio-processing-cleanup"
            title="Remove development / test uploads and stuck-processing rows"
          >
            <Sparkles className="w-3.5 h-3.5" /> Cleanup dev-test
          </button>
          <button
            type="button"
            onClick={onRefresh}
            className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border border-stone-border-soft bg-white hover:border-charcoal/40"
            data-testid="studio-processing-refresh"
          >
            <RefreshCw className="w-3.5 h-3.5" /> Refresh
          </button>
        </div>
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
          {uploads.map((u, i) => {
            const isSeed = !!u.demo_seed;
            const isPublished = u.status === "published";
            return (
              <div
                key={u.id}
                className="rounded-xl border border-stone-border-soft bg-white p-4 flex items-start justify-between gap-3 hover:border-charcoal/30 transition-colors"
                data-testid={`studio-upload-row-${i}`}
              >
                <button
                  type="button"
                  onClick={() => onOpenReview(u.id)}
                  className="text-left min-w-0 flex-1"
                  title="Open in Review Queue"
                >
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
                    {u.catalogue_brand && (
                      <span data-testid={`studio-upload-brand-${i}`}>
                        Brand · <span className="text-charcoal font-medium">{u.catalogue_brand}</span>
                      </span>
                    )}
                    <span className="truncate">
                      Uploaded · {new Date(u.created_at).toLocaleString()}
                    </span>
                  </div>
                  {u.region_rejects && Object.values(u.region_rejects).some((n) => n > 0) && (
                    <div
                      className="mt-1.5 flex flex-wrap items-center gap-1 text-[10px]"
                      data-testid={`studio-upload-rejects-${i}`}
                    >
                      <span className="uppercase tracking-widest text-warm-grey/70">
                        Region filter
                      </span>
                      {Object.entries(u.region_rejects)
                        .filter(([, n]) => n > 0)
                        .map(([cls, n]) => (
                          <span
                            key={cls}
                            className="px-1.5 py-0.5 rounded-full border border-stone-border-soft bg-stone-panel/40 text-charcoal font-mono"
                          >
                            {cls.toLowerCase().replace(/_/g, " ")} · {n}
                          </span>
                        ))}
                    </div>
                  )}
                  {u.failure_reason && (
                    <div
                      className="mt-2 text-[11px] text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-2 py-1"
                      data-testid={`studio-upload-failure-${i}`}
                    >
                      {u.failure_reason}
                    </div>
                  )}
                </button>
                <div className="flex flex-col items-end gap-1.5 shrink-0">
                  <StatusBadge status={u.status} records={u.records_extracted} />
                  {!isSeed && (
                    <div className="flex items-center gap-1 flex-wrap justify-end">
                      <button
                        type="button"
                        onClick={() => onReprocess(u)}
                        className="inline-flex items-center gap-1 text-[10px] uppercase tracking-widest text-warm-grey hover:text-charcoal px-2 py-1 rounded-full border border-stone-border-soft bg-white"
                        data-testid={`studio-upload-reprocess-${i}`}
                        title="Re-run extraction on the stored PDF"
                      >
                        <RefreshCw className="w-3 h-3" strokeWidth={1.75} /> Reprocess
                      </button>
                      <button
                        type="button"
                        onClick={() => onReplace(u)}
                        className="inline-flex items-center gap-1 text-[10px] uppercase tracking-widest text-warm-grey hover:text-charcoal px-2 py-1 rounded-full border border-stone-border-soft bg-white"
                        data-testid={`studio-upload-replace-${i}`}
                        title="Replace the PDF with a new edition"
                      >
                        <Upload className="w-3 h-3" strokeWidth={1.75} /> Replace
                      </button>
                      {isPublished ? (
                        <button
                          type="button"
                          onClick={() => onArchive(u)}
                          className="inline-flex items-center gap-1 text-[10px] uppercase tracking-widest text-warm-grey hover:text-charcoal px-2 py-1 rounded-full border border-stone-border-soft bg-white"
                          data-testid={`studio-upload-archive-${i}`}
                          title="Archive — records stop appearing in matching"
                        >
                          <Archive className="w-3 h-3" strokeWidth={1.75} /> Archive
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => onDelete(u)}
                          className="inline-flex items-center gap-1 text-[10px] uppercase tracking-widest text-rose-700 hover:bg-rose-50 px-2 py-1 rounded-full border border-rose-200 bg-white"
                          data-testid={`studio-upload-delete-${i}`}
                          title="Delete this upload and its records"
                        >
                          <Trash2 className="w-3 h-3" strokeWidth={1.75} /> Delete
                        </button>
                      )}
                    </div>
                  )}
                  {isSeed && (
                    <span className="text-[9px] uppercase tracking-widest text-warm-grey/70">
                      Reference · protected
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
      {nonSeedFailed > 0 && (
        <p className="text-[11px] text-warm-grey mt-1">
          {nonSeedFailed} failed upload{nonSeedFailed > 1 ? "s" : ""} can be removed individually or via <span className="text-charcoal font-semibold">Cleanup dev-test</span>.
        </p>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*                     Tab 3 — Review Queue (per upload)              */
/* ------------------------------------------------------------------ */
function ReviewTab({ uploads, selectedUploadId, setSelectedUploadId, onDelete }) {
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [acting, setActing] = useState(false);
  const [editing, setEditing] = useState(null);

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

  const recordAction = async (action, ids, confirmMsg) => {
    if (!ids || ids.length === 0) return;
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    setActing(true);
    try {
      await api.post("/admin/studio/records/bulk", { record_ids: ids, action });
      toast.success(`${action[0].toUpperCase()}${action.slice(1)}d ${ids.length} record(s)`);
      setSelected(new Set());
      await load(selectedUploadId);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setActing(false);
    }
  };

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
  // Select All must only pick DRAFT records per spec — never touches
  // already-published / archived / rejected rows.
  const draftRecords = records.filter((r) => r.status === "draft");
  const allDraftsSelected = draftRecords.length > 0 && draftRecords.every((r) => selected.has(r.id));
  const toggleSelectAll = () => {
    if (allDraftsSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(draftRecords.map((r) => r.id)));
    }
  };
  const [previewingPage, setPreviewingPage] = useState(null);

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
                onClick={toggleSelectAll}
                disabled={draftRecords.length === 0}
                className="text-xs px-3 py-1.5 rounded-full border border-stone-border-soft bg-white hover:border-charcoal/40 disabled:opacity-40"
                data-testid="studio-review-select-all"
              >
                {allDraftsSelected ? "Deselect all" : `Select all drafts (${draftRecords.length})`}
              </button>
              <button
                type="button"
                onClick={() => recordAction("delete", Array.from(selected), `Delete ${selected.size} record(s)? This cannot be undone.`)}
                disabled={selected.size === 0 || acting}
                className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border border-rose-200 text-rose-700 bg-white hover:bg-rose-50 disabled:opacity-40"
                data-testid="studio-review-delete-selected"
              >
                <Trash2 className="w-3.5 h-3.5" /> Delete ({selected.size})
              </button>
              <button
                type="button"
                onClick={() => recordAction("archive", Array.from(selected), `Archive ${selected.size} record(s)? They will stop appearing in matches.`)}
                disabled={selected.size === 0 || acting}
                className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border border-stone-border-soft text-charcoal bg-white hover:bg-stone-panel/40 disabled:opacity-40"
                data-testid="studio-review-archive-selected"
              >
                <Archive className="w-3.5 h-3.5" /> Archive ({selected.size})
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
                <Check className="w-3.5 h-3.5" /> Publish selected ({selected.size})
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
              <div className="inline-flex items-center gap-2 text-[10px] uppercase tracking-widest px-2 py-0.5 rounded-full border border-orange-200 bg-orange-50 text-orange-700 font-semibold mb-3">
                <AlertCircle className="w-3 h-3" strokeWidth={2} /> Needs attention
              </div>
              <div className="font-display text-base font-semibold text-charcoal mb-1">
                No records could be extracted from this upload.
              </div>
              <p className="text-warm-grey text-sm">
                {activeUpload?.failure_reason || "The catalogue layout was not recognised."}
              </p>
              <p className="text-warm-grey text-xs mt-2 mb-4">
                Use <b>Preview page</b> to inspect what the extractor saw, click <b>Reprocess</b> in the Processing Queue to retry, or delete this upload.
              </p>
              <div className="flex flex-wrap items-center justify-center gap-2">
                {activeUpload?.page_count > 0 && (
                  <button
                    type="button"
                    onClick={() => setPreviewingPage({ upload_id: activeUpload.id, page: 1 })}
                    className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border border-stone-border-soft bg-white hover:bg-stone-panel/40"
                    data-testid="studio-review-preview-empty"
                  >
                    Preview page 1
                  </button>
                )}
                {activeUpload && !activeUpload.demo_seed && (
                  <button
                    type="button"
                    onClick={() => onDelete && onDelete(activeUpload)}
                    className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border border-rose-200 text-rose-700 bg-white hover:bg-rose-50"
                    data-testid="studio-review-delete-failed"
                  >
                    <Trash2 className="w-3.5 h-3.5" /> Delete this upload
                  </button>
                )}
              </div>
            </div>
          ) : (
            <div className="grid md:grid-cols-2 gap-3" data-testid="studio-review-list">
              {records.map((r, i) => {
                const isSel = selected.has(r.id);
                const disabled = r.status !== "draft" && r.status !== "published";
                return (
                  <div
                    key={r.id}
                    className={`flex items-start gap-3 rounded-xl border p-3 bg-white transition-colors ${
                      disabled
                        ? "opacity-70 border-stone-border-soft"
                        : isSel
                        ? "border-charcoal/60 bg-stone-panel/40"
                        : "border-stone-border-soft hover:border-charcoal/30"
                    }`}
                    data-testid={`studio-review-row-${i}`}
                  >
                    <input
                      type="checkbox"
                      checked={isSel}
                      disabled={r.status === "rejected"}
                      onChange={() => toggle(r.id)}
                      className="mt-1 cursor-pointer"
                      data-testid={`studio-review-checkbox-${i}`}
                    />
                    <div
                      className="w-14 h-14 rounded-lg shrink-0 border border-stone-border-soft overflow-hidden"
                      style={{ backgroundColor: r.color_hex || "#B7ADA0" }}
                    >
                      {r.page_preview_b64 && (
                        <img
                          src={`data:image/jpeg;base64,${r.page_preview_b64}`}
                          alt=""
                          className="w-full h-full object-cover"
                        />
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-baseline justify-between gap-2">
                        <div className="min-w-0">
                          <div className="text-[10px] uppercase tracking-widest text-warm-grey/70 truncate">
                            {r.brand || "Unknown brand"} · Page {r.page_number}
                            {r.swatch_index_on_page ? ` · Swatch ${r.swatch_index_on_page}` : ""}
                          </div>
                          <div className="text-sm font-semibold text-charcoal truncate">
                            {r.material_name}
                          </div>
                          {r.collection_name && (
                            <div className="text-[10px] italic text-warm-grey truncate">
                              Collection · {r.collection_name}
                            </div>
                          )}
                        </div>
                        <div className="flex flex-col items-end gap-1">
                          <StatusBadge status={r.status} />
                          {r.needs_review && (
                            <span
                              className="text-[9px] uppercase tracking-widest px-1.5 py-0.5 rounded-full border border-orange-200 bg-orange-50 text-orange-700 font-semibold"
                              data-testid="studio-record-needs-review"
                            >
                              Needs review
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-warm-grey">
                        <span>
                          Cat · <span className="text-charcoal">{r.category}</span>
                          {r.category_hint_conflict && (
                            <span
                              className="ml-1 text-amber-700"
                              title="Detected category differs from the upload hint"
                            >
                              ⚠
                            </span>
                          )}
                        </span>
                        {r.material_code && (
                          <span>
                            Code · <span className="font-mono text-charcoal">{r.material_code}</span>
                          </span>
                        )}
                        <span>
                          Swatch · <span className="font-mono text-charcoal">{r.color_hex}</span>
                        </span>
                        {typeof r.confidence === "number" && (
                          <span>
                            Conf · <span className="text-charcoal">{r.confidence}%</span>
                          </span>
                        )}
                        {r.region_class && r.region_class !== "MATERIAL_SWATCH" && (
                          <span data-testid={`studio-record-region-${i}`}>
                            Region · <span className="text-charcoal">{r.region_class.toLowerCase().replace(/_/g, " ")}</span>
                          </span>
                        )}
                      </div>
                      {Array.isArray(r.needs_review_reasons) && r.needs_review_reasons.length > 0 && (
                        <div
                          className="mt-1 flex flex-wrap items-center gap-1"
                          data-testid={`studio-record-reasons-${i}`}
                        >
                          {r.needs_review_reasons.map((reason) => (
                            <span
                              key={reason}
                              className="text-[9px] uppercase tracking-widest px-1.5 py-0.5 rounded-full border border-amber-200 bg-amber-50 text-amber-800 font-medium"
                              title={`Flagged for review — ${reason.replace(/_/g, " ")}`}
                            >
                              {reason.replace(/_/g, " ")}
                            </span>
                          ))}
                        </div>
                      )}
                      <div className="mt-2 flex items-center gap-1 flex-wrap">
                        <button
                          type="button"
                          onClick={() => setEditing(r)}
                          className="text-[10px] uppercase tracking-widest text-warm-grey hover:text-charcoal px-2 py-0.5 rounded-full border border-stone-border-soft bg-white"
                          data-testid={`studio-record-edit-${i}`}
                        >
                          Edit
                        </button>
                        {r.status === "draft" && (
                          <button
                            type="button"
                            onClick={() => recordAction("publish", [r.id])}
                            className="text-[10px] uppercase tracking-widest text-white bg-charcoal hover:bg-charcoal/90 px-2 py-0.5 rounded-full border border-charcoal"
                            data-testid={`studio-record-publish-${i}`}
                          >
                            Publish
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => setPreviewingPage({ upload_id: r.upload_id, page: r.page_number })}
                          className="text-[10px] uppercase tracking-widest text-warm-grey hover:text-charcoal px-2 py-0.5 rounded-full border border-stone-border-soft bg-white"
                          data-testid={`studio-record-preview-${i}`}
                        >
                          Preview page
                        </button>
                        <button
                          type="button"
                          onClick={() => recordAction("delete", [r.id], `Delete "${r.material_name}"?`)}
                          className="text-[10px] uppercase tracking-widest text-rose-700 hover:bg-rose-50 px-2 py-0.5 rounded-full border border-rose-200 bg-white"
                          data-testid={`studio-record-delete-${i}`}
                        >
                          Delete
                        </button>
                        {r.status === "published" && (
                          <button
                            type="button"
                            onClick={() => recordAction("archive", [r.id], `Archive "${r.material_name}"? It will stop appearing in matching results.`)}
                            className="text-[10px] uppercase tracking-widest text-warm-grey hover:text-charcoal px-2 py-0.5 rounded-full border border-stone-border-soft bg-white"
                            data-testid={`studio-record-archive-${i}`}
                          >
                            Archive
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}
      {editing && (
        <EditRecordModal
          record={editing}
          onClose={() => setEditing(null)}
          onSaved={async () => { setEditing(null); await load(selectedUploadId); }}
        />
      )}
      {previewingPage && (
        <PreviewPageModal
          uploadId={previewingPage.upload_id}
          page={previewingPage.page}
          onClose={() => setPreviewingPage(null)}
        />
      )}
    </div>
  );
}


function PreviewPageModal({ uploadId, page, onClose }) {
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
    <div
      className="fixed inset-0 bg-charcoal/70 z-50 flex items-center justify-center p-4"
      onClick={onClose}
      data-testid="studio-preview-modal"
    >
      <div
        className="bg-white rounded-2xl border border-stone-border-soft p-4 max-w-4xl max-h-[90vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <div className="text-overline">Source page {page}</div>
          <button
            type="button"
            onClick={onClose}
            className="text-xs px-3 py-1 rounded-full border border-stone-border-soft hover:bg-stone-panel/40"
            data-testid="studio-preview-close"
          >
            Close
          </button>
        </div>
        {err && <div className="text-sm text-rose-700 p-4">{err}</div>}
        {!err && !img && <div className="text-sm text-warm-grey p-4">Loading preview…</div>}
        {img && (
          <img
            src={`data:image/jpeg;base64,${img}`}
            alt={`Page ${page}`}
            className="max-w-full h-auto rounded-lg border border-stone-border-soft"
          />
        )}
      </div>
    </div>
  );
}


function EditRecordModal({ record, onClose, onSaved }) {
  const [form, setForm] = useState({
    brand: record.brand || "",
    material_name: record.material_name || "",
    material_code: record.material_code || "",
    category: record.category || "",
    material_family: record.material_family || "",
    finish: record.finish || "",
    region: record.region || "",
    tags: (record.tags || []).join(", "),
    notes: record.notes || "",
  });
  const [saving, setSaving] = useState(false);
  const save = async () => {
    setSaving(true);
    try {
      const payload = { ...form };
      // Normalise the comma-separated tags string back into an array
      // before sending to the backend.
      payload.tags = String(form.tags || "")
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      await api.patch(`/admin/studio/records/${record.id}`, payload);
      toast.success("Record updated");
      await onSaved();
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setSaving(false);
    }
  };
  return (
    <div
      className="fixed inset-0 bg-charcoal/50 z-50 flex items-center justify-center p-4"
      onClick={onClose}
      data-testid="studio-edit-modal"
    >
      <div
        className="bg-white rounded-2xl border border-stone-border-soft p-6 w-full max-w-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-overline mb-1">Edit material record</div>
        <h3 className="font-display text-xl font-semibold text-charcoal mb-4">
          {record.material_name}
        </h3>
        <div className="grid grid-cols-2 gap-3">
          {["brand", "material_name", "material_code", "category", "material_family", "finish", "region"].map((k) => (
            <label key={k} className="text-xs text-warm-grey">
              <div className="mb-1 capitalize">{k.replace("_", " ")}</div>
              <input
                type="text"
                value={form[k]}
                onChange={(e) => setForm({ ...form, [k]: e.target.value })}
                className="w-full text-sm px-3 py-2 rounded-lg border border-stone-border-soft bg-white text-charcoal"
                data-testid={`studio-edit-field-${k}`}
              />
            </label>
          ))}
          <label className="text-xs text-warm-grey col-span-2">
            <div className="mb-1">Tags (comma-separated)</div>
            <input
              type="text"
              value={form.tags}
              onChange={(e) => setForm({ ...form, tags: e.target.value })}
              placeholder="matte, textured, indoor"
              className="w-full text-sm px-3 py-2 rounded-lg border border-stone-border-soft bg-white text-charcoal"
              data-testid="studio-edit-field-tags"
            />
          </label>
          <label className="text-xs text-warm-grey col-span-2">
            <div className="mb-1">Notes</div>
            <textarea
              rows={2}
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              className="w-full text-sm px-3 py-2 rounded-lg border border-stone-border-soft bg-white text-charcoal"
              data-testid="studio-edit-field-notes"
            />
          </label>
        </div>
        <div className="flex items-center justify-end gap-2 mt-5">
          <button type="button" onClick={onClose} className="text-xs px-3 py-1.5 rounded-full border border-stone-border-soft bg-white">
            Cancel
          </button>
          <button
            type="button"
            onClick={save}
            disabled={saving}
            className="text-xs px-3 py-1.5 rounded-full bg-charcoal text-white disabled:opacity-40"
            data-testid="studio-edit-save"
          >
            {saving ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*                    Tab 4 — Published Library                       */
/* ------------------------------------------------------------------ */
function LibraryTab({ uploads, onArchive }) {
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
                {(() => {
                  const parent = uploads.find((u) => u.id === r.upload_id);
                  if (!parent || parent.demo_seed) return null;
                  return (
                    <div className="mt-2">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.preventDefault();
                          onArchive && onArchive(parent);
                        }}
                        className="inline-flex items-center gap-1 text-[10px] uppercase tracking-widest text-warm-grey hover:text-charcoal px-2 py-1 rounded-full border border-stone-border-soft bg-white"
                        data-testid={`studio-library-archive-${i}`}
                        title="Archive the parent catalogue — records stop appearing in matching"
                      >
                        <Archive className="w-3 h-3" strokeWidth={1.75} /> Archive catalogue
                      </button>
                    </div>
                  );
                })()}
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
  // Support deep-link from Material Library: /admin/studio?tab=upload&category=Stone
  const initialTab = (typeof window !== "undefined"
    && new URLSearchParams(window.location.search).get("tab")) || "upload";
  const initialCategory = (typeof window !== "undefined"
    && new URLSearchParams(window.location.search).get("category")) || "";
  const [tab, setTab] = useState(initialTab);
  const [categoryHint, setCategoryHint] = useState(initialCategory);
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

  // Live polling — while ANY upload is still in `processing`, refresh
  // the upload list every 4s so the Processing Queue accurately reflects
  // backend state (background extraction terminal transitions). Stops
  // immediately once every row is in a terminal state. This is what
  // prevents "stuck on Processing / spinner frozen halfway" UI bugs
  // when the backend has already reached `review` / `failed`.
  const anyProcessing = useMemo(
    () => uploads.some((u) => u.status === "processing"),
    [uploads],
  );
  useEffect(() => {
    if (!anyProcessing) return;
    const t = setInterval(() => { loadUploads(); }, 4000);
    return () => clearInterval(t);
  }, [anyProcessing, loadUploads]);

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

  const deleteUpload = async (u) => {
    if (!u) return;
    const msg =
      u.records_extracted > 0
        ? `Delete "${u.filename}" and its ${u.records_extracted} record(s)? This cannot be undone.`
        : `Delete "${u.filename}"? This upload has no extracted records.`;
    if (!window.confirm(msg)) return;
    try {
      await api.delete(`/admin/studio/uploads/${u.id}`);
      toast.success(`Deleted ${u.filename}`);
      if (selectedUploadId === u.id) setSelectedUploadId("");
      await loadUploads();
    } catch (e) {
      toast.error(formatApiError(e));
    }
  };

  const archiveUpload = async (u) => {
    if (!u) return;
    if (
      !window.confirm(
        `Archive "${u.filename}"? Its records will no longer appear in the MaterialMatch Library or matching results. You can restore it later.`
      )
    )
      return;
    try {
      await api.post(`/admin/studio/uploads/${u.id}/archive`);
      toast.success(`Archived ${u.filename}`);
      await loadUploads();
    } catch (e) {
      toast.error(formatApiError(e));
    }
  };

  const runCleanup = async () => {
    if (
      !window.confirm(
        "Cleanup dev-test uploads? This removes any upload whose filename matches a development / test pattern (pub.pdf, rej.pdf, studio_test*, TESTBrand*…) and any upload stuck in 'processing' for more than 15 minutes. Reference catalogues and real supplier uploads are never touched."
      )
    )
      return;
    try {
      const r = await api.post("/admin/studio/cleanup");
      toast.success(
        `Cleanup complete — removed ${r.data.removed ?? 0} upload(s)` +
          (r.data.stuck_processing_removed
            ? `, including ${r.data.stuck_processing_removed} stuck-processing`
            : "")
      );
      await loadUploads();
    } catch (e) {
      toast.error(formatApiError(e));
    }
  };

  const reprocessUpload = async (u) => {
    if (!window.confirm(
      `Re-run extraction on "${u.filename}"? Existing draft, rejected and archived records will be replaced. Published records stay in place.`
    )) return;
    try {
      toast.info("Reprocessing catalogue — this may take up to 2 minutes for scanned PDFs…");
      const r = await api.post(`/admin/studio/uploads/${u.id}/reprocess`);
      toast.success(`Reprocessed ${u.filename} — ${r.data.records_extracted} record(s) extracted`);
      await loadUploads();
    } catch (e) {
      toast.error(formatApiError(e));
    }
  };

  const replaceUpload = async (u) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "application/pdf";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      if (!window.confirm(
        `Replace "${u.filename}" with "${file.name}"? All existing records for this catalogue will be deleted and extraction will re-run on the new file.`
      )) return;
      const form = new FormData();
      form.append("file", file);
      try {
        toast.info("Uploading replacement…");
        const r = await api.post(`/admin/studio/uploads/${u.id}/replace`, form);
        toast.success(`Replaced — ${r.data.records_extracted} record(s) extracted`);
        await loadUploads();
      } catch (e) {
        toast.error(formatApiError(e));
      }
    };
    input.click();
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
            const active = tab === t.id;
            // Swap the Processing tab's idle icon (`Layers`) for
            // `Loader2` ONLY while a job is actually running.  This is
            // the fix for the "stuck spinner" perception — a static
            // `Loader2` glyph looks identical to a paused spinner
            // whether or not the CSS animation is active.
            const isSpinning = t.id === "processing" && anyProcessing;
            const Icon = isSpinning ? Loader2 : t.icon;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => { setTab(t.id); loadUploads(); }}
                className={`inline-flex items-center gap-2 px-4 py-3 text-sm border-b-2 transition-colors whitespace-nowrap ${
                  active
                    ? "border-charcoal text-charcoal font-semibold"
                    : "border-transparent text-warm-grey hover:text-charcoal"
                }`}
                data-testid={`studio-tab-${t.id}`}
              >
                <Icon
                  className={`w-4 h-4 ${isSpinning ? "animate-spin" : ""}`}
                  strokeWidth={1.5}
                />
                {t.label}
              </button>
            );
          })}
        </div>

        {/* Tab body */}
        {tab === "upload" && (
          <UploadTab
            categoryHint={categoryHint}
            onClearCategoryHint={() => setCategoryHint("")}
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
            onDelete={deleteUpload}
            onArchive={archiveUpload}
            onCleanup={runCleanup}
            onReprocess={reprocessUpload}
            onReplace={replaceUpload}
          />
        )}
        {tab === "review" && (
          <ReviewTab
            uploads={uploads}
            selectedUploadId={selectedUploadId}
            setSelectedUploadId={setSelectedUploadId}
            onDelete={deleteUpload}
          />
        )}
        {tab === "library" && <LibraryTab uploads={uploads} onArchive={archiveUpload} />}
      </main>
    </div>
  );
}
