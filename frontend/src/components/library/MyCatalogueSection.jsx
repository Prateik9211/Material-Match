import React, { useCallback, useEffect, useRef, useState } from "react";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { UploadCloud, FileText, Trash2, CheckCircle2, AlertCircle, Loader2 } from "lucide-react";

// 2026-02-01 (round 4) — user-uploadable catalogues.
// Drives POST /api/library/uploads (drag-drop PDF), GET /api/library/uploads
// (list) and DELETE /api/library/uploads/{id}. Extraction is fire-and-
// forget on the backend so we poll status until the row lands in a
// terminal state (published | failed).

const STATUS_META = {
  processing: {
    label: "Processing",
    Icon: Loader2,
    cls: "text-ochre bg-ochre-soft border-ochre/30",
    spin: true,
  },
  published: {
    label: "Ready",
    Icon: CheckCircle2,
    cls: "text-sage bg-sage-soft border-sage/30",
  },
  failed: {
    label: "Failed",
    Icon: AlertCircle,
    cls: "text-red-600 bg-red-50 border-red-200",
  },
};

function StatusPill({ status }) {
  const m = STATUS_META[status] || STATUS_META.processing;
  const { Icon, spin } = m;
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-[11px] uppercase tracking-widest font-semibold px-2 py-0.5 rounded-full border ${m.cls}`}
      data-testid={`user-upload-status-${status}`}
    >
      <Icon className={`w-3 h-3 ${spin ? "animate-spin" : ""}`} strokeWidth={2} />
      {m.label}
    </span>
  );
}

function UploadRow({ item, onDelete }) {
  const dt = item.created_at ? new Date(item.created_at) : null;
  return (
    <div
      className="flex items-start justify-between gap-4 border border-stone-border-soft rounded-xl px-4 py-3 bg-white hover:border-stone-border transition-colors"
      data-testid={`user-upload-row-${item.id}`}
    >
      <div className="flex items-start gap-3 min-w-0">
        <div className="w-9 h-9 rounded-lg bg-stone-panel grid place-items-center flex-shrink-0">
          <FileText className="w-4 h-4 text-charcoal" strokeWidth={1.5} />
        </div>
        <div className="min-w-0">
          <div className="font-medium text-charcoal truncate max-w-[38ch]" title={item.filename}>
            {item.filename}
          </div>
          <div className="text-[11px] text-warm-grey flex items-center gap-2 mt-0.5 flex-wrap">
            <StatusPill status={item.status} />
            {typeof item.records_extracted === "number" && item.status === "published" && (
              <span data-testid={`user-upload-records-${item.id}`}>
                {item.records_extracted} record{item.records_extracted === 1 ? "" : "s"}
              </span>
            )}
            {dt && <span>{dt.toLocaleString()}</span>}
            {item.status === "failed" && item.failure_reason && (
              <span
                className="text-red-600 truncate max-w-[42ch]"
                title={item.failure_reason}
                data-testid={`user-upload-error-${item.id}`}
              >
                — {item.failure_reason}
              </span>
            )}
          </div>
        </div>
      </div>
      <button
        type="button"
        onClick={() => onDelete(item)}
        className="text-warm-grey hover:text-red-600 hover:bg-red-50 rounded-full w-8 h-8 grid place-items-center transition-colors flex-shrink-0"
        aria-label={`Delete ${item.filename}`}
        title={`Delete "${item.filename}" and all extracted records`}
        data-testid={`user-upload-delete-${item.id}`}
      >
        <Trash2 className="w-4 h-4" strokeWidth={1.5} />
      </button>
    </div>
  );
}

export default function MyCatalogueSection({ initialFocusUpload = false }) {
  const [uploads, setUploads] = useState([]);
  const [quota, setQuota] = useState(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/library/uploads");
      setUploads(data.uploads || []);
      setQuota(data.quota || null);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Poll every 4 s while any upload is still processing.
  useEffect(() => {
    const anyProcessing = uploads.some((u) => u.status === "processing");
    if (!anyProcessing) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [uploads, load]);

  const uploadFile = useCallback(async (file) => {
    if (!file) return;
    if (!/\.pdf$/i.test(file.name)) {
      toast.error("Only PDF supplier catalogues are accepted.");
      return;
    }
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const { data } = await api.post("/library/uploads", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      toast.success(`"${file.name}" uploaded — extraction started.`);
      // Optimistic add so the user immediately sees the row spinning.
      setUploads((cur) => [
        {
          id: data.upload_id,
          filename: file.filename || file.name,
          status: "processing",
          created_at: new Date().toISOString(),
          size_bytes: file.size,
          records_extracted: 0,
        },
        ...cur,
      ]);
      // Refetch after a short delay so the real server row replaces
      // the optimistic one.
      setTimeout(load, 1500);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setUploading(false);
    }
  }, [load]);

  const onDelete = useCallback(async (item) => {
    const ok = window.confirm(
      `Delete "${item.filename}" from your catalogue?\n\nThis permanently removes the PDF and every material extracted from it. This cannot be undone.`
    );
    if (!ok) return;
    try {
      const { data } = await api.delete(`/library/uploads/${item.id}`);
      setUploads((cur) => cur.filter((x) => x.id !== item.id));
      toast.success(`Removed "${item.filename}" (${data.records_deleted} record${data.records_deleted === 1 ? "" : "s"}).`);
      load();
    } catch (e) {
      toast.error(formatApiError(e));
    }
  }, [load]);

  const quotaFull = quota && quota.used >= quota.max;
  const maxMb = quota ? Math.round(quota.max_bytes / (1024 * 1024)) : 25;

  return (
    <div className="space-y-4" data-testid="my-catalogue-section">
      {/* Upload dropzone */}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const f = e.dataTransfer.files?.[0];
          if (f) uploadFile(f);
        }}
        className={`rounded-2xl border-2 border-dashed p-6 sm:p-8 text-center transition-all ${
          dragOver
            ? "border-charcoal bg-sand/60"
            : "border-stone-border bg-stone-panel/50 hover:border-stone-border hover:bg-stone-panel/70"
        } ${quotaFull ? "opacity-60" : ""}`}
        data-testid="user-upload-dropzone"
      >
        <UploadCloud className="w-8 h-8 text-charcoal/70 mx-auto mb-3" strokeWidth={1.25} />
        <div className="font-display text-lg text-charcoal font-semibold mb-1">
          {quotaFull ? "Upload limit reached" : "Drop a supplier PDF here"}
        </div>
        <div className="text-xs text-warm-grey mb-4">
          {quotaFull
            ? `You've reached your ${quota.max}-catalogue limit. Delete an existing one to free a slot.`
            : `Or click below. PDF only · max ${maxMb} MB · up to ${quota?.max || 20} catalogues.`}
        </div>
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={uploading || quotaFull}
          className="inline-flex items-center gap-2 bg-charcoal text-paper hover:bg-charcoal/85 disabled:opacity-60 disabled:cursor-not-allowed rounded-full px-5 py-2.5 text-sm font-medium transition-colors"
          data-testid="user-upload-choose-btn"
        >
          {uploading ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" strokeWidth={1.75} />
              Uploading…
            </>
          ) : (
            <>
              <UploadCloud className="w-4 h-4" strokeWidth={1.75} />
              Choose a PDF
            </>
          )}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) uploadFile(f);
            e.target.value = "";
          }}
          data-testid="user-upload-file-input"
        />
        {quota && !quotaFull && (
          <div className="text-[11px] text-warm-grey/70 mt-3" data-testid="user-upload-quota">
            {quota.used} of {quota.max} catalogues used
          </div>
        )}
      </div>

      {/* Uploaded list */}
      {loading ? (
        <div className="text-center text-sm text-warm-grey py-6">Loading your catalogues…</div>
      ) : uploads.length === 0 ? (
        <div
          className="text-center py-8 text-sm text-warm-grey border border-stone-border-soft rounded-xl bg-white"
          data-testid="my-uploads-empty"
        >
          You haven&apos;t uploaded any catalogues yet. Drop a supplier PDF above to get started —
          we&apos;ll extract every swatch and colour, and your library will be searchable within seconds.
        </div>
      ) : (
        <div className="space-y-2" data-testid="my-uploads-list">
          {uploads.map((u) => (
            <UploadRow key={u.id} item={u} onDelete={onDelete} />
          ))}
        </div>
      )}
    </div>
  );
}
