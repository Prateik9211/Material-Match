import React from "react";
import { Sparkles, MapPin, ChevronRight, BookOpen } from "lucide-react";

const ROOM_TYPE_LABEL = {
  living: "Living Room", bedroom: "Bedroom", kitchen: "Kitchen",
  bath: "Bathroom", dining: "Dining", office: "Office",
  kids: "Kids", outdoor: "Outdoor", hallway: "Hallway", custom: "Room",
};

const IMAGE_KIND_LABEL = {
  reference: "Reference",
  moodboard: "Moodboard",
  final_render: "Final Render",
};

/* ---------------- utilities ---------------- */
function ImageGridReadOnly({ items, resolveUrl, testidPrefix, aspect = "aspect-[4/3]" }) {
  const [urls, setUrls] = React.useState({});
  React.useEffect(() => {
    let mounted = true;
    items.forEach(async (img) => {
      if (urls[img.id]) return;
      const u = await resolveUrl(img);
      if (mounted && u) setUrls((prev) => ({ ...prev, [img.id]: u }));
    });
    return () => { mounted = false; };
  }, [items]);
  if (!items || items.length === 0) return null;
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 sm:gap-6">
      {items.map((img, i) => (
        <div
          key={img.id}
          className={`${aspect} bg-stone-panel rounded-2xl overflow-hidden border border-stone-border-soft shadow-soft relative group`}
          data-testid={`${testidPrefix}-${i}`}
        >
          {img._label && (
            <span className="absolute top-3 left-3 z-10 text-[10px] font-mono tracking-widest uppercase px-2.5 py-1 bg-paper/95 backdrop-blur text-charcoal rounded-full">
              {img._label}
            </span>
          )}
          {urls[img.id] && (
            <img
              src={urls[img.id]}
              alt=""
              loading="lazy"
              className="w-full h-full object-cover"
            />
          )}
        </div>
      ))}
    </div>
  );
}

function StorySection({ number, overline, title, subtitle, children, testid }) {
  return (
    <section className="scroll-mt-20 break-inside-avoid" data-testid={testid}>
      <div className="mb-8">
        <div className="flex items-baseline gap-3">
          <div className="text-xs font-mono text-warm-grey/50 tabular-nums">{number}</div>
          <div>
            <div className="text-overline">{overline}</div>
            <h2 className="font-display text-3xl sm:text-4xl font-bold tracking-tight text-charcoal">
              {title}
            </h2>
          </div>
        </div>
        {subtitle && (
          <p className="text-sm text-warm-grey mt-3 max-w-2xl leading-relaxed pl-8">
            {subtitle}
          </p>
        )}
      </div>
      {children}
    </section>
  );
}

/* ---------------- material spec card ---------------- */
function findCatalogueMatchForZone(zoneName, materialType, catalogueMatches) {
  if (!catalogueMatches || catalogueMatches.length === 0) return null;
  const zn = (zoneName || "").toLowerCase();
  const mt = (materialType || "").toLowerCase();
  // Prefer explicit zone match, then material_name overlap
  return catalogueMatches.find((m) => {
    const mn = (m.material_name || "").toLowerCase();
    return (m.zone && m.zone.toLowerCase() === zn) ||
           (zn && mn.includes(zn.split(" ")[0])) ||
           (mt && mn.includes(mt.split(" ")[0]));
  }) || null;
}

function SpecCard({ row, index, catalogueMatch }) {
  const confidence = typeof row.confidence === "number" ? row.confidence : null;
  return (
    <article
      className="bg-white border border-stone-border-soft rounded-2xl overflow-hidden shadow-soft hover:shadow-hover transition-shadow break-inside-avoid"
      data-testid={`present-spec-${index}`}
    >
      {/* Header strip */}
      <div className="bg-stone-panel px-5 py-3 border-b border-stone-border-soft flex items-baseline justify-between">
        <div className="text-overline" data-testid={`present-spec-zone-${index}`}>
          {row.zone || row.surface || `Zone ${index + 1}`}
        </div>
        {confidence !== null && (
          <span className="text-[10px] font-mono text-warm-grey">{confidence}%</span>
        )}
      </div>

      <div className="p-5 space-y-4">
        <div>
          <div className="text-[10px] uppercase tracking-widest text-warm-grey/70">Material Type</div>
          <div className="font-display text-lg font-semibold text-charcoal leading-tight mt-0.5">
            {row.material_type || row.material_name || row.material || row.material_family || "Material"}
          </div>
          {row.material_family && row.material_type && (
            <div className="text-[11px] text-warm-grey mt-0.5">Family · {row.material_family}</div>
          )}
        </div>

        <div className="grid grid-cols-2 gap-x-4 gap-y-3 text-xs">
          {row.finish && (
            <div>
              <div className="text-[10px] uppercase tracking-widest text-warm-grey/70">Finish</div>
              <div className="text-charcoal mt-0.5">{row.finish}</div>
            </div>
          )}
          {row.texture && (
            <div>
              <div className="text-[10px] uppercase tracking-widest text-warm-grey/70">Texture</div>
              <div className="text-charcoal mt-0.5">{row.texture}</div>
            </div>
          )}
          {row.color && (
            <div>
              <div className="text-[10px] uppercase tracking-widest text-warm-grey/70">Color</div>
              <div className="text-charcoal mt-0.5">{row.color}</div>
            </div>
          )}
          {row.procurement_difficulty && (
            <div>
              <div className="text-[10px] uppercase tracking-widest text-warm-grey/70">Procurement</div>
              <div className="text-charcoal mt-0.5">{row.procurement_difficulty}</div>
            </div>
          )}
        </div>

        {/* Catalogue match block — shown when it exists */}
        {catalogueMatch && (
          <div className="rounded-xl bg-sage-soft/60 border border-sage/30 p-3.5" data-testid={`present-spec-catalogue-${index}`}>
            <div className="flex items-center gap-1.5 mb-1">
              <BookOpen className="w-3.5 h-3.5 text-sage" strokeWidth={2} />
              <span className="text-[10px] uppercase tracking-widest text-sage font-semibold">
                Catalogue Match
              </span>
              <span className="ml-auto text-xs font-mono font-semibold text-sage">
                {catalogueMatch.match_percent}%
              </span>
            </div>
            <div className="text-sm text-charcoal font-medium leading-tight">
              {catalogueMatch.material_name}
            </div>
            <div className="text-[11px] text-warm-grey mt-1">
              {catalogueMatch.filename}
              {catalogueMatch.page_number && <> · page {catalogueMatch.page_number}</>}
            </div>
            {catalogueMatch.explanation && (
              <p className="text-[11px] text-charcoal/70 mt-1.5 leading-relaxed italic">
                {catalogueMatch.explanation}
              </p>
            )}
          </div>
        )}

        {/* Regional sourcing fallback — only when there is no catalogue match */}
        {!catalogueMatch && (row.local_alternative || row.indian_alternative || (row.brands_to_check && row.brands_to_check.length > 0) || row.vendor_type) && (
          <div className="rounded-xl bg-ochre-soft/60 border border-ochre/30 p-3.5" data-testid={`present-spec-local-${index}`}>
            <div className="flex items-center gap-1.5 mb-1">
              <MapPin className="w-3.5 h-3.5 text-ochre" strokeWidth={2} />
              <span className="text-[10px] uppercase tracking-widest text-ochre font-semibold">
                Recommended Local Options
              </span>
            </div>
            {(row.local_alternative || row.indian_alternative) && (
              <div className="text-sm text-charcoal leading-relaxed">
                {row.local_alternative || row.indian_alternative}
              </div>
            )}
            {row.brands_to_check && row.brands_to_check.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-2">
                {row.brands_to_check.slice(0, 6).map((b) => (
                  <span key={b} className="text-[10px] px-2 py-0.5 rounded-full bg-paper text-charcoal border border-stone-border-soft">
                    {b}
                  </span>
                ))}
              </div>
            )}
            {(row.vendor_type || (row.sourcing_keywords && row.sourcing_keywords.length > 0)) && (
              <div className="text-[11px] text-warm-grey mt-2 space-y-0.5">
                {row.vendor_type && <div>Vendor · {row.vendor_type}</div>}
                {row.sourcing_keywords && row.sourcing_keywords.length > 0 && (
                  <div className="italic">
                    Search: {row.sourcing_keywords.slice(0, 3).join(" · ")}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </article>
  );
}

/* ---------------- product card ---------------- */
function ProductCard({ product, index }) {
  const matched = product.matched_affiliate;
  return (
    <article
      className="bg-white border border-stone-border-soft rounded-2xl overflow-hidden shadow-soft hover:shadow-hover transition-shadow flex flex-col break-inside-avoid"
      data-testid={`present-product-${index}`}
    >
      <div className="bg-stone-panel px-5 py-3 border-b border-stone-border-soft flex items-baseline justify-between">
        <span className="text-overline">{product.category}</span>
        {typeof product.confidence === "number" && (
          <span className="text-[10px] font-mono text-warm-grey">{product.confidence}%</span>
        )}
      </div>
      <div className="p-5 flex-1 flex flex-col gap-2.5">
        <h3 className="font-display text-lg font-semibold text-charcoal leading-tight">
          {product.product_name}
        </h3>
        {product.description && (
          <p className="text-xs text-warm-grey leading-relaxed line-clamp-3">
            {product.description}
          </p>
        )}
        <div className="flex flex-wrap gap-1">
          {(product.material_keywords || []).slice(0, 2).map((m) => (
            <span key={m} className="text-[10px] px-2 py-0.5 rounded-md bg-stone-panel text-charcoal border border-stone-border-soft">
              {m}
            </span>
          ))}
          {(product.finish_keywords || []).slice(0, 2).map((m) => (
            <span key={m} className="text-[10px] px-2 py-0.5 rounded-md bg-sand/40 text-charcoal">
              {m}
            </span>
          ))}
        </div>
        {product.estimated_price_inr && (
          <div className="text-sm text-charcoal font-semibold mt-auto">
            {product.estimated_price_inr}
          </div>
        )}
        {matched && (
          <a
            href={matched.affiliate_url}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-1 rounded-lg bg-sage-soft/60 border border-sage/30 p-2.5 hover:bg-sage-soft transition-colors"
          >
            <div className="flex items-center gap-1.5 mb-0.5">
              <Sparkles className="w-3 h-3 text-sage" strokeWidth={2} />
              <span className="text-[10px] uppercase tracking-widest text-sage font-semibold">
                Curated recommendation
              </span>
            </div>
            <div className="text-xs text-charcoal font-medium leading-tight">
              {matched.product_name}
            </div>
            <div className="text-[10px] text-warm-grey mt-0.5">
              {matched.platform}
              {matched.price_inr && <> · {matched.price_inr}</>}
            </div>
          </a>
        )}
      </div>
    </article>
  );
}

/* ---------------- cover page ---------------- */
function CoverPage({ room, projectName, clientName, designerName, updatedAt }) {
  const dateStr = (updatedAt ? new Date(updatedAt) : new Date()).toLocaleDateString(undefined, {
    year: "numeric", month: "long", day: "numeric",
  });
  return (
    <section
      className="min-h-[70vh] mb-16 pb-14 border-b border-stone-border-soft flex flex-col justify-between page-break-after"
      data-testid="story-cover"
    >
      <div>
        <div className="text-overline mb-4">Concept Presentation</div>
        <h1 className="font-display text-5xl sm:text-7xl font-bold tracking-tight text-charcoal leading-[0.95] max-w-3xl">
          {projectName || room.name}
        </h1>
        {clientName && (
          <p className="text-lg sm:text-xl text-warm-grey mt-6 font-display italic">
            For {clientName}
          </p>
        )}
      </div>
      <div className="mt-16 grid grid-cols-2 sm:grid-cols-3 gap-6 text-sm">
        <div>
          <div className="text-[10px] uppercase tracking-widest text-warm-grey/70 mb-1">Room</div>
          <div className="text-charcoal font-medium">
            {room.name} · <span className="text-warm-grey">{ROOM_TYPE_LABEL[room.room_type] || "Room"}</span>
          </div>
        </div>
        {designerName && (
          <div>
            <div className="text-[10px] uppercase tracking-widest text-warm-grey/70 mb-1">Designer</div>
            <div className="text-charcoal font-medium">{designerName}</div>
          </div>
        )}
        <div>
          <div className="text-[10px] uppercase tracking-widest text-warm-grey/70 mb-1">Date</div>
          <div className="text-charcoal font-medium">{dateStr}</div>
        </div>
      </div>
      <div className="mt-14 text-xs text-warm-grey">
        Prepared with MaterialMatch
      </div>
    </section>
  );
}

/* ---------------- MAIN ---------------- */
/**
 * Sprint 5A Presentation. New order per spec:
 *   [Cover] → Room Title
 *   01 Existing Space
 *   02 Design Direction  (merges reference + moodboard + final render with labels)
 *   03 Concept Overview
 *   04 Material Specifications  (with Catalogue Match OR Indian recommendations)
 *   05 Products & Fixtures
 *   06 Designer Notes
 * Empty sections auto-hide in client-facing view.
 *
 * Props:
 *  - room: { name, room_type, current_site_photos, moodboards, reference_images,
 *           final_render_images, concept_overview, designer_notes }
 *  - specs, products, catalogueMatches
 *  - resolveImageUrl(image, kind) -> Promise<string>
 *  - printMode: boolean
 *  - showCover: boolean (default true; the editor's preview can pass false)
 */
export default function RoomPresentation({
  room,
  specs = [],
  products = [],
  catalogueMatches = [],
  resolveImageUrl,
  printMode = false,
  showCover = true,
  projectName,
  clientName,
  designerName,
}) {
  if (!room) return null;
  const empty = (arr) => !arr || arr.length === 0;
  const noOverview = !room.concept_overview || !room.concept_overview.trim();
  const noNotes = !room.designer_notes || !room.designer_notes.trim();

  // Merge design direction sources (reference / moodboard / final render) with type labels
  const designDirection = [
    ...(room.reference_images || []).map((i) => ({ ...i, _kind: "reference", _label: IMAGE_KIND_LABEL.reference })),
    ...(room.moodboards || []).map((i) => ({ ...i, _kind: "moodboard", _label: IMAGE_KIND_LABEL.moodboard })),
    ...(room.final_render_images || []).map((i) => ({ ...i, _kind: "final_render", _label: IMAGE_KIND_LABEL.final_render })),
  ];

  const resolveGeneric = (img) => resolveImageUrl(img, img._kind || "reference");
  const resolveExisting = (img) => resolveImageUrl(img, "current_site");

  const wrapCls = printMode
    ? "bg-paper text-charcoal max-w-4xl mx-auto px-8 py-10 print-story"
    : "bg-paper text-charcoal max-w-5xl mx-auto px-6 sm:px-10 py-14";

  return (
    <article className={wrapCls} data-testid="room-presentation">
      {showCover && (
        <CoverPage
          room={room}
          projectName={projectName}
          clientName={clientName}
          designerName={designerName}
          updatedAt={room.updated_at}
        />
      )}

      {/* Room title (compact — the big title lives on the cover) */}
      {!showCover && (
        <header className="mb-14 pb-8 border-b border-stone-border-soft">
          <div className="text-overline mb-2">{ROOM_TYPE_LABEL[room.room_type] || "Room"}</div>
          <h1 className="font-display text-4xl sm:text-6xl font-bold tracking-tight leading-[1.05] text-charcoal">
            {room.name}
          </h1>
        </header>
      )}

      <div className="space-y-20">
        {!empty(room.current_site_photos) && (
          <StorySection
            number="01"
            overline="Step one"
            title="Existing Space"
            subtitle="Current room photos uploaded by the designer."
            testid="story-existing"
          >
            <ImageGridReadOnly
              items={room.current_site_photos}
              resolveUrl={resolveExisting}
              testidPrefix="story-existing-img"
              aspect="aspect-[4/3]"
            />
          </StorySection>
        )}

        {designDirection.length > 0 && (
          <StorySection
            number={empty(room.current_site_photos) ? "01" : "02"}
            overline={empty(room.current_site_photos) ? "Step one" : "Step two"}
            title="Design Direction"
            subtitle="References, moodboards, and renders that guide the concept."
            testid="story-direction"
          >
            <ImageGridReadOnly
              items={designDirection}
              resolveUrl={resolveGeneric}
              testidPrefix="story-direction-img"
              aspect="aspect-[4/3]"
            />
          </StorySection>
        )}

        {!noOverview && (
          <StorySection
            number="03"
            overline="Step three"
            title="Concept Overview"
            testid="story-overview"
          >
            <p
              className="font-display text-xl sm:text-2xl leading-relaxed text-charcoal max-w-3xl whitespace-pre-wrap"
              data-testid="story-overview-text"
            >
              {room.concept_overview}
            </p>
          </StorySection>
        )}

        {specs.length > 0 && (
          <StorySection
            number="04"
            overline="Step four"
            title="Material Specifications"
            subtitle="Surface materials with catalogue matches or local sourcing options."
            testid="story-specs"
          >
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
              {specs.map((r, i) => {
                const zoneName = r.zone || r.surface;
                const materialType = r.material_type || r.material_name;
                const match = findCatalogueMatchForZone(zoneName, materialType, catalogueMatches);
                return (
                  <SpecCard
                    key={r.id || r.row_id || r.zone || i}
                    row={r}
                    index={i}
                    catalogueMatch={match}
                  />
                );
              })}
            </div>
          </StorySection>
        )}

        {products.length > 0 && (
          <StorySection
            number="05"
            overline="Step five"
            title="Products & Fixtures"
            subtitle="Curated products to bring the concept to life."
            testid="story-products"
          >
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
              {products.map((p, i) => <ProductCard key={p.id || i} product={p} index={i} />)}
            </div>
          </StorySection>
        )}

        {!noNotes && (
          <StorySection
            number="06"
            overline="Step six"
            title="Designer Notes"
            testid="story-notes"
          >
            <p
              className="text-base sm:text-lg leading-relaxed text-charcoal/85 max-w-3xl whitespace-pre-wrap"
              data-testid="story-notes-text"
            >
              {room.designer_notes}
            </p>
          </StorySection>
        )}
      </div>

      <footer className="mt-24 pt-6 border-t border-stone-border-soft flex items-center justify-between text-xs text-warm-grey/70">
        <div className="inline-flex items-center gap-1.5">
          Prepared with MaterialMatch <ChevronRight className="w-3 h-3" strokeWidth={2} />
        </div>
        {room.updated_at && (
          <div>Updated {new Date(room.updated_at).toLocaleDateString()}</div>
        )}
      </footer>
    </article>
  );
}
