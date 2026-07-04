import React, { useRef, useState } from "react";
import { Crop, X, Sparkles, Loader2 } from "lucide-react";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";

/**
 * Sprint 7 — Google-Lens-style interactive region selection.
 * Overlay on top of the reference image. Drag to draw a rectangle,
 * then click "Analyze this area" — client-side crops and POSTs to
 * /api/projects/{id}/analyze-region and returns rows for the right panel.
 */
export default function RegionSelector({ projectId, imgSrc, onAnalyzed, pins, focusedPinIndex, onHoverPin }) {
  const wrapRef = useRef(null);
  const imgRef = useRef(null);
  const [mode, setMode] = useState("view"); // view | draw
  const [rect, setRect] = useState(null); // {x, y, w, h} in wrapper %
  const [dragging, setDragging] = useState(null);
  const [busy, setBusy] = useState(false);

  const startDraw = (e) => {
    if (mode !== "draw") return;
    const b = wrapRef.current.getBoundingClientRect();
    const x = ((e.clientX - b.left) / b.width) * 100;
    const y = ((e.clientY - b.top) / b.height) * 100;
    setDragging({ x0: x, y0: y });
    setRect({ x, y, w: 0, h: 0 });
  };
  const moveDraw = (e) => {
    if (!dragging) return;
    const b = wrapRef.current.getBoundingClientRect();
    const x = ((e.clientX - b.left) / b.width) * 100;
    const y = ((e.clientY - b.top) / b.height) * 100;
    const x0 = Math.min(dragging.x0, x);
    const y0 = Math.min(dragging.y0, y);
    const w = Math.abs(x - dragging.x0);
    const h = Math.abs(y - dragging.y0);
    setRect({ x: x0, y: y0, w, h });
  };
  const endDraw = () => setDragging(null);

  const cropAndAnalyze = async () => {
    if (!rect || rect.w < 2 || rect.h < 2) {
      toast.error("Draw a rectangle first");
      return;
    }
    setBusy(true);
    try {
      const img = imgRef.current;
      if (!img || !img.naturalWidth) throw new Error("Reference image not ready");
      const sx = (rect.x / 100) * img.naturalWidth;
      const sy = (rect.y / 100) * img.naturalHeight;
      const sw = (rect.w / 100) * img.naturalWidth;
      const sh = (rect.h / 100) * img.naturalHeight;
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(64, Math.round(sw));
      canvas.height = Math.max(64, Math.round(sh));
      const ctx = canvas.getContext("2d");
      ctx.drawImage(img, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
      const dataUrl = canvas.toDataURL("image/jpeg", 0.9);
      const b64 = dataUrl.split(",", 2)[1] || "";
      const { data } = await api.post(`/projects/${projectId}/analyze-region`, {
        crop_b64: b64,
        note: "user-selected region",
      });
      onAnalyzed({ rows: data.rows || [], summary: data.summary || {}, crop_data_url: dataUrl });
      toast.success(`Detected ${(data.rows || []).length} material${(data.rows || []).length === 1 ? "" : "s"} in your selection`);
      setMode("view");
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  const clear = () => { setRect(null); setDragging(null); };

  return (
    <div className="space-y-3" data-testid="region-selector">
      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          onClick={() => { setMode(mode === "draw" ? "view" : "draw"); clear(); }}
          className={`inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border transition-colors ${
            mode === "draw" ? "bg-charcoal text-paper border-charcoal" : "bg-white text-charcoal border-stone-border hover:border-charcoal"
          }`}
          data-testid="region-toggle-btn"
        >
          <Crop className="w-3.5 h-3.5" strokeWidth={1.75} />
          {mode === "draw" ? "Cancel selection" : "Select area of interest"}
        </button>
        {rect && rect.w > 2 && (
          <>
            <button
              type="button"
              onClick={cropAndAnalyze}
              disabled={busy}
              className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full bg-charcoal text-paper border border-charcoal hover:bg-charcoal/85 disabled:opacity-60"
              data-testid="region-analyze-btn"
            >
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" strokeWidth={1.75} />}
              {busy ? "Analyzing area…" : "Analyze this area"}
            </button>
            <button type="button" onClick={clear} className="inline-flex items-center gap-1 text-xs text-warm-grey hover:text-charcoal">
              <X className="w-3 h-3" strokeWidth={1.75} /> Clear
            </button>
          </>
        )}
        <span className="text-[10px] text-warm-grey ml-auto">
          {mode === "draw" ? "Drag on the image to select an area." : "Google-Lens style · beta"}
        </span>
      </div>
      <div
        ref={wrapRef}
        className={`relative rounded-2xl overflow-hidden bg-stone-panel border border-stone-border-soft ${mode === "draw" ? "cursor-crosshair" : "cursor-default"}`}
        onMouseDown={startDraw}
        onMouseMove={moveDraw}
        onMouseUp={endDraw}
        onMouseLeave={endDraw}
        data-testid="region-canvas"
      >
        <img ref={imgRef} src={imgSrc} alt="Reference" crossOrigin="anonymous" className="w-full h-auto block select-none pointer-events-none" />
        {/* Sprint 2 Revision — numbered pins linking image ↔ material card. */}
        {mode !== "draw" && !rect && Array.isArray(pins) && pins.map((p, i) => {
          if (!p || typeof p.x !== "number" || typeof p.y !== "number") return null;
          const isFocused = focusedPinIndex === i;
          return (
            <button
              type="button"
              key={i}
              onMouseEnter={() => onHoverPin && onHoverPin(i)}
              onMouseLeave={() => onHoverPin && onHoverPin(null)}
              className={`absolute -translate-x-1/2 -translate-y-1/2 rounded-full font-mono text-[10px] font-semibold grid place-items-center transition-all shadow-hover ${
                isFocused
                  ? "w-8 h-8 bg-charcoal text-paper ring-4 ring-paper/70 z-20"
                  : "w-6 h-6 bg-paper/95 text-charcoal border border-charcoal/40 hover:bg-charcoal hover:text-paper z-10"
              }`}
              style={{ left: `${p.x}%`, top: `${p.y}%` }}
              data-testid={`image-pin-${i}`}
              aria-label={p.label || `Zone ${i + 1}`}
              title={p.label || `Zone ${i + 1}`}
            >
              {i + 1}
            </button>
          );
        })}
        {rect && rect.w > 0.2 && rect.h > 0.2 && (
          <div
            className="absolute border-2 border-charcoal rounded-md pointer-events-none"
            style={{
              left: `${rect.x}%`, top: `${rect.y}%`, width: `${rect.w}%`, height: `${rect.h}%`,
              boxShadow: "0 0 0 9999px rgba(43,39,36,0.32)",
            }}
            data-testid="region-rect"
          >
            <div className="absolute -top-6 left-0 text-[10px] uppercase tracking-widest bg-charcoal text-paper px-2 py-0.5 rounded font-semibold">
              Selection
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
