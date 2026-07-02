import React from "react";
import { Layers } from "lucide-react";

/**
 * Left rail on the Match page — reference image preview + selected-material spec card.
 * Receives only what it needs to render; no data fetching.
 */
export default function MatchSidebar({
  project,
  refImg,
  imgError,
  onImgError,
  selected,
}) {
  return (
    <aside className="lg:col-span-5">
      <div className="lg:sticky lg:top-24 space-y-6">
        <div
          className="bg-white border border-black/5 rounded-2xl overflow-hidden shadow-soft"
          data-testid="match-reference-card"
        >
          {refImg && !imgError ? (
            <img
              src={refImg}
              alt="Reference"
              className="w-full aspect-[4/3] object-cover"
              onError={onImgError}
            />
          ) : (
            <div className="w-full aspect-[4/3] bg-[#F5F1EC] grid place-items-center text-overline">
              {imgError ? "Image unavailable" : "No reference"}
            </div>
          )}
          <div className="p-5">
            <div className="text-overline mb-2">Reference</div>
            <h3 className="font-display font-semibold">{project?.name}</h3>
            {project?.client_name && (
              <p className="text-xs text-neutral-500 mt-0.5">{project.client_name}</p>
            )}
          </div>
        </div>

        <div
          className="bg-white border border-black/5 rounded-2xl p-5 shadow-soft"
          data-testid="match-selected-material"
        >
          <div className="flex items-center gap-2 mb-4">
            <Layers className="w-4 h-4 text-neutral-700" strokeWidth={1.5} />
            <div className="text-overline">Selected material</div>
          </div>
          <h3 className="font-display text-xl font-semibold mb-3">{selected.zone}</h3>
          <dl className="space-y-2 text-sm">
            {[
              ["Material", selected.material_type],
              ["Color", selected.color],
              ["Texture", selected.texture],
              ["Finish", selected.finish],
              ["Style", selected.design_style],
            ].map(([label, value]) => (
              <div
                key={label}
                className="flex items-start justify-between gap-4 pb-2 border-b border-black/5 last:border-0"
              >
                <dt className="text-neutral-500 text-xs">{label}</dt>
                <dd className="text-neutral-900 text-right">{value || "—"}</dd>
              </div>
            ))}
          </dl>
          {selected.keywords?.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-4">
              {selected.keywords.slice(0, 6).map((k, i) => (
                <span
                  key={`k-${k}-${i}`}
                  className="text-[10px] px-2 py-0.5 rounded-full bg-[#F5F1EC] text-neutral-600"
                >
                  {k}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}
