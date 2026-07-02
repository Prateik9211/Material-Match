import React, { useEffect, useState } from "react";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { Sparkles, Save, Loader2 } from "lucide-react";
import ImageGallery from "./ImageGallery";

const ROOM_TYPES = [
  ["living", "Living"], ["bedroom", "Bedroom"], ["kitchen", "Kitchen"],
  ["bath", "Bath"], ["dining", "Dining"], ["office", "Office"],
  ["kids", "Kids"], ["outdoor", "Outdoor"], ["hallway", "Hallway"], ["custom", "Custom"],
];

function Section({ number, overline, title, subtitle, children, testid }) {
  return (
    <section className="bg-white border border-black/5 rounded-2xl shadow-soft p-6 sm:p-8" data-testid={testid}>
      <div className="flex items-baseline gap-3 mb-4">
        <div className="text-xs font-mono text-neutral-400 tabular-nums">{number}</div>
        <div>
          <div className="text-overline">{overline}</div>
          <h3 className="font-display text-xl font-semibold tracking-tight">{title}</h3>
          {subtitle && <p className="text-xs text-neutral-500 mt-1">{subtitle}</p>}
        </div>
      </div>
      {children}
    </section>
  );
}

/**
 * Editor for a single room. Autosaves nothing — the user clicks Save at the
 * top for text fields; image uploads/deletes and pin toggles autosave.
 *
 * Props:
 *  - room: full room object from GET /rooms/{id}
 *  - specs: [ { id, ... } ] project's material rows (from mock_analysis)
 *  - products: [ { id, ... } ] project's detected products
 *  - onChange: (updatedRoomFields) => void — parent updates its state
 */
export default function RoomEditor({ room, specs, products, onChange }) {
  const [name, setName] = useState(room.name || "");
  const [roomType, setRoomType] = useState(room.room_type || "custom");
  const [overview, setOverview] = useState(room.concept_overview || "");
  const [notes, setNotes] = useState(room.designer_notes || "");
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const pinnedSpecs = new Set(room.pinned_material_row_ids || []);
  const pinnedProds = new Set(room.pinned_product_ids || []);

  // Reset local state when switching rooms
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
      toast.success("Room saved");
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
      toast.success("Draft ready — review and edit before saving");
    } catch (e) {
      toast.error(formatApiError(e));
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="space-y-6" data-testid="room-editor">
      {/* Meta bar */}
      <div className="bg-white border border-black/5 rounded-2xl shadow-soft p-5 flex flex-wrap items-end gap-4">
        <div className="flex-1 min-w-[220px]">
          <label className="text-[10px] uppercase tracking-widest text-neutral-500 font-semibold">Room name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 w-full border-b border-black/10 focus:border-black focus:outline-none py-1.5 font-display text-xl"
            data-testid="room-name-input"
          />
        </div>
        <div>
          <label className="text-[10px] uppercase tracking-widest text-neutral-500 font-semibold">Type</label>
          <select
            value={roomType}
            onChange={(e) => setRoomType(e.target.value)}
            className="mt-1 block border border-neutral-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-black bg-white"
            data-testid="room-type-select"
          >
            {ROOM_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>
        <button
          type="button"
          onClick={saveMeta}
          disabled={saving}
          className="inline-flex items-center gap-2 bg-black text-white rounded-full px-5 py-2 text-sm font-medium hover:bg-black/80 disabled:opacity-60"
          data-testid="save-room-btn"
        >
          <Save className="w-4 h-4" strokeWidth={1.5} />
          {saving ? "Saving…" : "Save"}
        </button>
      </div>

      {/* 01 Current Space */}
      <Section
        number="01"
        overline="Step one"
        title="Current Space"
        subtitle="Before photos — how the room looks today."
        testid="editor-section-current"
      >
        <ImageGallery
          roomId={room.id}
          kind="current_site"
          images={room.current_site_photos}
          onChange={(imgs) => onChange({ ...room, current_site_photos: imgs })}
          accent="bg-[#F3F2EE]"
          testidPrefix="current"
        />
      </Section>

      {/* 02 Moodboards */}
      <Section
        number="02"
        overline="Step two"
        title="Moodboards"
        subtitle="Curated collages that capture the design direction."
        testid="editor-section-moodboards"
      >
        <ImageGallery
          roomId={room.id}
          kind="moodboard"
          images={room.moodboards}
          onChange={(imgs) => onChange({ ...room, moodboards: imgs })}
          testidPrefix="moodboard"
        />
      </Section>

      {/* 03 Reference Images */}
      <Section
        number="03"
        overline="Step three"
        title="Reference Images"
        subtitle="Aspirational references that inform the specification."
        testid="editor-section-references"
      >
        <ImageGallery
          roomId={room.id}
          kind="reference"
          images={room.reference_images}
          onChange={(imgs) => onChange({ ...room, reference_images: imgs })}
          testidPrefix="reference"
        />
      </Section>

      {/* 04 Concept Overview */}
      <Section
        number="04"
        overline="Step four"
        title="Concept Overview"
        subtitle="Draft a client-facing paragraph. Generate a starter draft, then edit and approve — it's your voice on the page."
        testid="editor-section-overview"
      >
        <div className="flex items-center gap-2 mb-3">
          <button
            type="button"
            onClick={generateOverview}
            disabled={generating}
            className="inline-flex items-center gap-2 bg-neutral-900 text-white rounded-full px-4 py-1.5 text-xs font-medium hover:bg-black disabled:opacity-60"
            data-testid="generate-overview-btn"
          >
            {generating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" strokeWidth={1.75} />}
            {generating ? "Drafting…" : "Generate concept draft"}
          </button>
          <span className="text-xs text-neutral-500">You always edit and approve.</span>
        </div>
        <textarea
          value={overview}
          onChange={(e) => setOverview(e.target.value)}
          rows={6}
          placeholder="A calm, considered space where…"
          className="w-full border border-neutral-200 rounded-lg p-4 text-base leading-relaxed focus:outline-none focus:border-black font-display"
          data-testid="overview-textarea"
        />
      </Section>

      {/* 05 Material Specifications */}
      <Section
        number="05"
        overline="Step five"
        title="Material Specifications"
        subtitle="Pin the surface materials this room should carry into the presentation."
        testid="editor-section-specs"
      >
        {specs.length === 0 ? (
          <p className="text-sm text-neutral-500 italic">
            No materials analyzed yet — run &ldquo;Generate specification&rdquo; on the project first.
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
                      ? "border-black bg-neutral-900 text-white"
                      : "border-neutral-200 hover:border-black/40 bg-white"
                  }`}
                  data-testid={`pin-spec-${rid}`}
                >
                  <div className="text-[10px] uppercase tracking-widest opacity-70">
                    {row.zone || row.surface || `Zone ${i + 1}`}
                  </div>
                  <div className="font-display font-semibold text-base mt-1">
                    {row.material_type || row.material_name || row.material || row.material_family || "Material"}
                  </div>
                  <div className={`text-xs mt-1 ${pinned ? "text-white/70" : "text-neutral-500"}`}>
                    {[row.finish, row.color].filter(Boolean).join(" · ")}
                  </div>
                  <div className={`text-[10px] mt-2 ${pinned ? "text-white" : "text-neutral-400"}`}>
                    {pinned ? "✓ Pinned" : "Click to pin"}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </Section>

      {/* 06 Suggested Products */}
      <Section
        number="06"
        overline="Step six"
        title="Suggested Products"
        subtitle="Pin the products & fixtures to include in the client presentation."
        testid="editor-section-products"
      >
        {products.length === 0 ? (
          <p className="text-sm text-neutral-500 italic">
            No products detected yet — run &ldquo;Generate specification&rdquo; on the project first.
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
                      ? "border-black bg-neutral-900 text-white"
                      : "border-neutral-200 hover:border-black/40 bg-white"
                  }`}
                  data-testid={`pin-product-${pid}`}
                >
                  <div className="text-[10px] uppercase tracking-widest opacity-70">
                    {p.category}
                  </div>
                  <div className="font-display font-semibold text-base mt-1">
                    {p.product_name}
                  </div>
                  {p.estimated_price_inr && (
                    <div className={`text-xs mt-1 ${pinned ? "text-white/70" : "text-neutral-500"}`}>
                      {p.estimated_price_inr}
                    </div>
                  )}
                  <div className={`text-[10px] mt-2 ${pinned ? "text-white" : "text-neutral-400"}`}>
                    {pinned ? "✓ Pinned" : "Click to pin"}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </Section>

      {/* 07 Designer Notes */}
      <Section
        number="07"
        overline="Step seven"
        title="Designer Notes"
        subtitle="Anything else the client should know — process, timelines, personal touches."
        testid="editor-section-notes"
      >
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={4}
          placeholder="A note from the designer…"
          className="w-full border border-neutral-200 rounded-lg p-4 text-sm leading-relaxed focus:outline-none focus:border-black"
          data-testid="notes-textarea"
        />
      </Section>
    </div>
  );
}
