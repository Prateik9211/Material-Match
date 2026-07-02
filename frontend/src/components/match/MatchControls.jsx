import React from "react";
import UploadZone from "@/components/UploadZone";
import { Sparkles, RefreshCw } from "lucide-react";

/**
 * Right-column control panel: prompt, PDF & image upload zones, Run Match button.
 */
export default function MatchControls({
  prompt,
  onPromptChange,
  pdfFiles,
  imgFiles,
  onAddPdfs,
  onRemovePdf,
  onAddImgs,
  onRemoveImg,
  busy,
  progressStep,
  hasResults,
  realMatchActive,
  onRunMatch,
}) {
  return (
    <div
      className="bg-white border border-black/5 rounded-2xl p-6 shadow-soft space-y-6"
      data-testid="match-controls-panel"
    >
      <div>
        <label className="text-overline">Optional prompt</label>
        <textarea
          rows={3}
          value={prompt}
          onChange={(e) => onPromptChange(e.target.value)}
          placeholder="e.g. Prefer FSC-certified options under £80/m². Avoid glossy finishes."
          className="mt-2 w-full bg-white border border-black/10 rounded-xl px-4 py-3.5 focus:outline-none focus:ring-2 focus:ring-black/20 transition-all text-sm resize-none"
          data-testid="match-prompt-input"
        />
      </div>

      <div className="grid sm:grid-cols-2 gap-6">
        <UploadZone
          label="Catalogue PDF"
          description="First 8 pages will be analysed"
          accept="application/pdf"
          multiple
          files={pdfFiles}
          onFiles={onAddPdfs}
          onRemove={onRemovePdf}
          testid="match-upload-pdf"
        />
        <UploadZone
          label="Product images"
          description="Optional · JPEG / PNG / WEBP"
          accept="image/jpeg,image/png,image/webp"
          multiple
          files={imgFiles}
          onFiles={onAddImgs}
          onRemove={onRemoveImg}
          testid="match-upload-images"
        />
      </div>

      <p className="text-xs text-neutral-500 italic" data-testid="match-pdf-note">
        PDF catalogue matching is experimental. Clean product / material images give better accuracy.
      </p>

      <div className="flex items-center gap-3 pt-2 border-t border-black/5">
        <button
          onClick={onRunMatch}
          disabled={busy}
          className="inline-flex items-center justify-center gap-2 bg-black text-white hover:bg-black/80 rounded-full px-7 py-3.5 font-medium transition-colors disabled:opacity-60"
          data-testid="run-match-btn"
        >
          {hasResults ? (
            <>
              <RefreshCw className={`w-4 h-4 ${busy ? "animate-spin" : ""}`} strokeWidth={1.5} />
              {busy ? "Re-matching…" : "Re-run match"}
            </>
          ) : (
            <>
              <Sparkles className={`w-4 h-4 ${busy ? "animate-pulse" : ""}`} strokeWidth={1.5} />
              {busy ? "Matching…" : "Run Match"}
            </>
          )}
        </button>
        {busy && progressStep && (
          <div className="text-sm text-neutral-500" data-testid="match-progress">
            {progressStep}
          </div>
        )}
        <span className="text-xs text-neutral-400 ml-auto" data-testid="match-mode-label">
          {realMatchActive ? "AI matching" : "Mock matching"}
        </span>
      </div>
    </div>
  );
}
