import React, { useEffect, useState } from "react";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { Sparkles, Save, Loader2, Camera, Palette, Image as ImageIcon } from "lucide-react";
import ImageGallery from "./ImageGallery";

const ROOM_TYPES = [
  ["living", "Living"], ["bedroom", "Bedroom"], ["kitchen", "Kitchen"],
  ["bath", "Bath"], ["dining", "Dining"], ["office", "Office"],
  ["kids", "Kids"], ["outdoor", "Outdoor"], ["hallway", "Hallway"], ["custom", "Custom"],
];

function Section({ number, overline, title, subtitle, children, testid }) {
  return (
    <section className="bg-white border border-stone-border-soft rounded-2xl shadow-soft p-6 sm:p-8" data-testid={testid}>
      <div className="flex items-baseline gap-3 mb-5">
        <div className="text-xs font-mono text-warm-grey/50 tabular-nums">{number}</div>
        <div>
          <div className="text-overline">{overline}</div>
          <h3 className="font-display text-xl font-semibold tracking-tight text-charcoal">{title}</h3>
          {subtitle && <p className="text-xs text-warm-grey mt-1 max-w-2xl">{subtitle}</p>}
        </div>
      </div>
      {children}
    </section>
  );
}

function DesignDirectionTab({ label, active, onClick, count, testid }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
        active
          ? "bg-charcoal text-paper"
          : "bg-stone-panel text-charcoal hover:bg-sand/50"
      }`}
      data-testid={testid}
    >
      {label}
      <span className={`text-[10px] font-mono ${active ? "text-paper/70" : "text-warm-grey"}`}>
        {count}
      </span>
    </button>
  );
}

/**
 * Sprint 5A editor — new section order:
 *  01 Existing Space
 *  02 Design Direction (Reference / Moodboard / Final Render — 3 sub-galleries)
 *  03 Concept Overview
 *  04 Material Specifications
 *  05 Products & Fixtures
 *  06 Designer Notes
 */
export default function RoomEditor({ room, specs, products, onChange }) {
  const [name, setName] = useState(room.name || "");
  const [roomType, setRoomType] = useState(room.room_type || "custom");
  const [overview, setOverview] = useState(room.concept_overview || "");
  const [notes, setNotes] = useState(room.designer_notes || "");
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [directionTab, setDirectionTab] = useState("reference");
  const pinnedSpecs = new Set(room.pinned_material_row_ids || []);
  const pinnedProds = new Set(room.pinned_product_ids || []);

  useEffect(() => {
    setName(room.name || "");
    setRoomType(room.room_type || "custom");
    setOverview(room.concept_overview || "");
    setNotes(room.designer_notes || "");
  }, [room.id]);

  const patchRoom = async (partial) => {
    try {
      const { data } = await api.patch(`/rooms/${room.id}`, partial);
      onChange(data);
    } catch (e) {
      toast.error(formatApiError(e));
    }
  };

  const saveMeta = async () => {
    setSaving(true);
    try {
      await patchRoom({
        name: name.trim() || "Untitled",
        room_type: roomType,
        concept_overview: overview,
        designer_notes: notes,
      });
      toast.success("Presentation updated");
    } finally {
      setSaving(false);
    }
  };

  const togglePinnedSpec = async (rowId) => {
    const next = new Set(pinnedSpecs);
    if (next.has(rowId)) next.delete(rowId); else next.add(rowId);
    await patchRoom({ pinned_material_row_ids: Array.from(next) });
  };

  const togglePinnedProduct = async (prodId) => {
    const next = new Set(pinnedProds);
    if (next.has(prodId)) next.delete(prodId); else next.add(prodId);
    await patchRoom({ pinned_product_ids: Array.from(next) });
  };

  const generateOverview = async () => {
    setGenerating(true);
    try {
      const { data } = await api.post(`/rooms/${room.id}/generate-overview`);
      setOverview(data.draft || "");
      toast.success("Draft ready — review, edit and save");
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setGenerating(false);
    }
  };

  // Design direction sub-gallery config
  const directionConfig = {
    reference: { kind: "reference", label: "Reference", images: room.reference_images || [], setter: (imgs) => onChange({ ...room, reference_images: imgs }), accent: "bg-stone-panel" },
    moodboard: { kind: "moodboard", label: "Moodboard", images: room.moodboards || [], setter: (imgs) => onChange({ ...room, moodboards: imgs }), accent: "bg-sand/40" },
    final_render: { kind: "final_render", label: "Final Render", images: room.final_render_images || [], setter: (imgs) => onChange({ ...room, final_render_images: imgs }), accent: "bg-sage-soft/40" },
  };
  const dir = directionConfig[directionTab];

  return (
    <div className="space-y-6" data-testid="room-editor">
      {/* Meta bar */}
      <div className="bg-white border border-stone-border-soft rounded-2xl shadow-soft p-5 flex flex-wrap items-end gap-4">
        <div className="flex-1 min-w-[220px]">
          <label className="text-[10px] uppercase tracking-widest text-warm-grey/70 font-semibold">Room name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 w-full border-b border-stone-border focus:border-charcoal focus:outline-none py-1.5 font-display text-xl bg-transparent"
            data-testid="room-name-input"
          />
        </div>
        <div>
          <label className="text-[10px] uppercase tracking-widest text-warm-grey/70 font-semibold">Type</label>
          <select
            value={roomType}
            onChange={(e) => setRoomType(e.target.value)}
            className="mt-1 block border border-stone-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-charcoal bg-white"
            data-testid="room-type-select"
          >
            {ROOM_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>
        <button
          type="button"
          onClick={saveMeta}
          disabled={saving}
          className="inline-flex items-center gap-2 bg-charcoal text-paper rounded-full px-5 py-2 text-sm font-medium hover:bg-charcoal/85 disabled:opacity-60"
          data-testid="save-room-btn"
        >
          <Save className="w-4 h-4" strokeWidth={1.5} />
          {saving ? "Saving…" : "Save"}
        </button>
      </div>

      {/* 01 Existing Space */}
      <Section
        number="01"
        overline="Step one"
        title="Existing Space"
        subtitle="Current room photos uploaded by designer or client."
        testid="editor-section-existing"
      >
        <div className="flex items-center gap-2 mb-3">
          <Camera className="w-3.5 h-3.5 text-warm-grey" strokeWidth={1.5} />
          <span className="text-xs text-warm-grey">Before photos of the current room</span>
        </div>
        <ImageGallery
          roomId={room.id}
          kind="current_site"
          images={room.current_site_photos}
          onChange={(imgs) => onChange({ ...room, current_site_photos: imgs })}
          accent="bg-stone-panel"
          testidPrefix="current"
        />
      </Section>

      {/* 02 Design Direction */}
      <Section
        number="02"
        overline="Step two"
        title="Design Direction"
        subtitle="Upload references, moodboards, or a final render. You can add any combination."
        testid="editor-section-direction"
      >
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <Palette className="w-3.5 h-3.5 text-warm-grey" strokeWidth={1.5} />
          <span className="text-xs text-warm-grey mr-2">Direction type:</span>
          <DesignDirectionTab
            label="Reference"
            active={directionTab === "reference"}
            onClick={() => setDirectionTab("reference")}
            count={directionConfig.reference.images.length}
            testid="direction-tab-reference"
          />
          <DesignDirectionTab
            label="Moodboard"
            active={directionTab === "moodboard"}
            onClick={() => setDirectionTab("moodboard")}
            count={directionConfig.moodboard.images.length}
            testid="direction-tab-moodboard"
          />
          <DesignDirectionTab
            label="Final Render"
            active={directionTab === "final_render"}
            onClick={() => setDirectionTab("final_render")}
            count={directionConfig.final_render.images.length}
            testid="direction-tab-final_render"
          />
        </div>
        <ImageGallery
          key={directionTab /* remount on switch */}
          roomId={room.id}
          kind={dir.kind}
          images={dir.images}
          onChange={dir.setter}
          accent={dir.accent}
          testidPrefix={`direction-${directionTab}`}
        />
      </Section>

      {/* 03 Concept Overview */}
      <Section
        number="03"
        overline="Step three"
        title="Concept Overview"
        subtitle="A short client-facing paragraph. Draft one, then edit and approve — it stays in your voice."
        testid="editor-section-overview"
      >
        <div className="flex items-center gap-2 mb-3 flex-wrap">
          <button
            type="button"
            onClick={generateOverview}
            disabled={generating}
            className="inline-flex items-center gap-2 bg-charcoal text-paper rounded-full px-4 py-1.5 text-xs font-medium hover:bg-charcoal/85 disabled:opacity-60"
            data-testid="generate-overview-btn"
          >
            {generating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" strokeWidth={1.75} />}
            {generating ? "Drafting…" : "Generate concept draft"}
          </button>
          <span className="text-xs text-warm-grey">You always edit and approve.</span>
        </div>
        <textarea
          value={overview}
          onChange={(e) => setOverview(e.target.value)}
          rows={6}
          placeholder="This living room direction uses warm neutrals, soft textures, and wood accents to create a calm, premium space…"
          className="w-full border border-stone-border rounded-xl p-4 text-base leading-relaxed focus:outline-none focus:border-charcoal font-display bg-paper-warm"
          data-testid="overview-textarea"
        />
      </Section>

      {/* 04 Material Specifications */}
      <Section
        number="04"
        overline="Step four"
        title="Material Specifications"
        subtitle="Pin the surface materials to carry into the presentation. Auto-selected on room creation — refine as needed."
        testid="editor-section-specs"
      >
        {specs.length === 0 ? (
          <p className="text-sm text-warm-grey italic">
            Run &ldquo;Generate specification&rdquo; on the project first to unlock material pinning.
          </p>
        ) : (
          <div className="grid sm:grid-cols-2 gap-3">
            {specs.map((row, i) => {
              const rid = String(row.id || row.row_id || row.zone || i);
              const pinned = pinnedSpecs.has(rid);
              return (
                <button
                  key={rid}
                  type="button"
                  onClick={() => togglePinnedSpec(rid)}
                  className={`text-left p-4 rounded-xl border transition-colors ${
                    pinned
                      ? "border-charcoal bg-charcoal text-paper"
                      : "border-stone-border hover:border-charcoal/40 bg-white"
                  }`}
                  data-testid={`pin-spec-${rid}`}
                >
                  <div className="text-[10px] uppercase tracking-widest opacity-70">
                    {row.zone || row.surface || `Zone ${i + 1}`}
                  </div>
                  <div className="font-display font-semibold text-base mt-1">
                    {row.material_type || row.material_name || row.material || row.material_family || "Material"}
                  </div>
                  <div className={`text-xs mt-1 ${pinned ? "text-paper/70" : "text-warm-grey"}`}>
                    {[row.finish, row.color].filter(Boolean).join(" · ")}
                  </div>
                  <div className={`text-[10px] mt-2 ${pinned ? "text-paper" : "text-warm-grey/60"}`}>
                    {pinned ? "✓ Pinned" : "Click to pin"}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </Section>

      {/* 05 Products & Fixtures */}
      <Section
        number="05"
        overline="Step five"
        title="Products & Fixtures"
        subtitle="Pin the products to include in the client presentation."
        testid="editor-section-products"
      >
        {products.length === 0 ? (
          <p className="text-sm text-warm-grey italic">
            Run &ldquo;Generate specification&rdquo; on the project first to detect products.
          </p>
        ) : (
          <div className="grid sm:grid-cols-2 gap-3">
            {products.map((p, i) => {
              const pid = String(p.id || i);
              const pinned = pinnedProds.has(pid);
              return (
                <button
                  key={pid}
                  type="button"
                  onClick={() => togglePinnedProduct(pid)}
                  className={`text-left p-4 rounded-xl border transition-colors ${
                    pinned
                      ? "border-charcoal bg-charcoal text-paper"
                      : "border-stone-border hover:border-charcoal/40 bg-white"
                  }`}
                  data-testid={`pin-product-${pid}`}
                >
                  <div className="text-[10px] uppercase tracking-widest opacity-70">{p.category}</div>
                  <div className="font-display font-semibold text-base mt-1">{p.product_name}</div>
                  {p.estimated_price_inr && (
                    <div className={`text-xs mt-1 ${pinned ? "text-paper/70" : "text-warm-grey"}`}>
                      {p.estimated_price_inr}
                    </div>
                  )}
                  <div className={`text-[10px] mt-2 ${pinned ? "text-paper" : "text-warm-grey/60"}`}>
                    {pinned ? "✓ Pinned" : "Click to pin"}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </Section>

      {/* 06 Designer Notes */}
      <Section
        number="06"
        overline="Step six"
        title="Designer Notes"
        subtitle="Anything else the client should know — process, timelines, personal touches."
        testid="editor-section-notes"
      >
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={4}
          placeholder="A note from the designer…"
          className="w-full border border-stone-border rounded-xl p-4 text-sm leading-relaxed focus:outline-none focus:border-charcoal bg-paper-warm"
          data-testid="notes-textarea"
        />
      </Section>
    </div>
  );
}
