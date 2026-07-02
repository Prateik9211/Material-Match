import React from "react";
import { Sparkles } from "lucide-react";

const ROOM_TYPE_LABEL = {
  living: "Living Room", bedroom: "Bedroom", kitchen: "Kitchen",
  bath: "Bathroom", dining: "Dining", office: "Office",
  kids: "Kids", outdoor: "Outdoor", hallway: "Hallway", custom: "Room",
};

/**
 * A section wrapper — used for every step of the visual story.
 * Shows a step number, small overline title, and children.
 */
function StorySection({ number, overline, title, children, testid }) {
  return (
    <section className="scroll-mt-20" data-testid={testid}>
      <div className="flex items-baseline gap-3 mb-6">
        <div className="text-xs font-mono text-neutral-400 tabular-nums">{number}</div>
        <div>
          <div className="text-overline">{overline}</div>
          <h2 className="font-display text-2xl sm:text-3xl font-semibold tracking-tight">
            {title}
          </h2>
        </div>
      </div>
      {children}
    </section>
  );
}

function ImageGridReadOnly({ images, resolveUrl, testidPrefix }) {
  const [urls, setUrls] = React.useState({});
  React.useEffect(() => {
    let mounted = true;
    images.forEach(async (img) => {
      if (urls[img.id]) return;
      const u = await resolveUrl(img);
      if (mounted && u) setUrls((prev) => ({ ...prev, [img.id]: u }));
    });
    return () => { mounted = false; };
  }, [images]);
  if (!images || images.length === 0) return null;
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
      {images.map((img, i) => (
        <div
          key={img.id}
          className="aspect-[4/3] bg-neutral-100 rounded-xl overflow-hidden border border-black/5"
          data-testid={`${testidPrefix}-${i}`}
        >
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

function SpecCard({ row, index }) {
  return (
    <div
      className="bg-white border border-black/5 rounded-xl p-4"
      data-testid={`present-spec-${index}`}
    >
      <div className="flex items-baseline justify-between gap-3 mb-2">
        <div className="text-overline">{row.zone || row.surface || `Zone ${index + 1}`}</div>
        {typeof row.confidence === "number" && (
          <span className="text-[10px] font-mono text-neutral-500">{row.confidence}%</span>
        )}
      </div>
      <div className="font-display text-lg font-semibold text-neutral-900 leading-tight">
        {row.material_type || row.material_name || row.material || row.material_family || "Material"}
      </div>
      <div className="text-xs text-neutral-500 mt-1 space-y-0.5">
        {row.finish && <div>Finish · {row.finish}</div>}
        {row.color && <div>Color · {row.color}</div>}
        {(row.material_family || row.family) && <div>Family · {row.material_family || row.family}</div>}
      </div>
      {row.indian_alternative && (
        <div className="mt-2 text-xs italic text-amber-800 border-l-2 border-amber-200 pl-2">
          {row.indian_alternative}
        </div>
      )}
    </div>
  );
}

function ProductCard({ product, index }) {
  const matched = product.matched_affiliate;
  return (
    <div
      className="bg-white border border-black/5 rounded-xl p-4 flex flex-col gap-2"
      data-testid={`present-product-${index}`}
    >
      <div className="flex items-baseline justify-between">
        <span className="inline-flex text-[10px] px-2 py-0.5 rounded-full bg-black text-white uppercase tracking-wider">
          {product.category}
        </span>
        {product.estimated_price_inr && (
          <span className="text-xs text-neutral-600">{product.estimated_price_inr}</span>
        )}
      </div>
      <div className="font-display text-base font-semibold leading-tight">
        {product.product_name}
      </div>
      {product.description && (
        <p className="text-xs text-neutral-500 line-clamp-2">{product.description}</p>
      )}
      {matched && (
        <a
          href={matched.affiliate_url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-1 inline-flex items-center gap-1 text-xs text-emerald-800 hover:underline"
        >
          <Sparkles className="w-3 h-3" strokeWidth={2} />
          {matched.product_name} · {matched.platform}
        </a>
      )}
    </div>
  );
}

/**
 * The 7-step Concept Presentation. Renders exactly the requested order:
 *   Current Space → Moodboards → References → Concept Overview
 *   → Material Specs → Suggested Products → Designer Notes
 *
 * Props:
 *  - room: { name, room_type, current_site_photos, moodboards, reference_images,
 *           concept_overview, designer_notes }
 *  - specs: [ { zone, material_name, finish, color, family, confidence } ]
 *  - products: [ { product_name, category, ... } ]
 *  - resolveImageUrl(image, kind) -> Promise<string> — return data-url for an image id
 *  - printMode: boolean — hides interactive chrome & switches to print-friendly styles
 */
export default function RoomPresentation({
  room,
  specs = [],
  products = [],
  resolveImageUrl,
  printMode = false,
  projectName,
  clientName,
}) {
  if (!room) return null;
  const typeLabel = ROOM_TYPE_LABEL[room.room_type] || "Room";
  const empty = (arr) => !arr || arr.length === 0;
  const noOverview = !room.concept_overview || !room.concept_overview.trim();
  const noNotes = !room.designer_notes || !room.designer_notes.trim();

  const wrapCls = printMode
    ? "bg-white text-neutral-900 max-w-4xl mx-auto px-8 py-10 print-story"
    : "bg-white text-neutral-900 max-w-5xl mx-auto px-6 sm:px-10 py-14";

  return (
    <article className={wrapCls} data-testid="room-presentation">
      {/* Hero */}
      <header className="mb-14 pb-8 border-b border-black/10">
        <div className="text-overline mb-2">{typeLabel}</div>
        <h1 className="font-display text-4xl sm:text-6xl font-bold tracking-tight leading-[1.05]">
          {room.name}
        </h1>
        {(projectName || clientName) && (
          <p className="text-sm text-neutral-500 mt-4">
            {projectName}
            {clientName ? ` · ${clientName}` : ""}
          </p>
        )}
      </header>

      <div className="space-y-16">
        {!empty(room.current_site_photos) && (
          <StorySection number="01" overline="Step one" title="Current Space" testid="story-current">
            <p className="text-sm text-neutral-500 mb-4 max-w-2xl">
              How the space looks today, before intervention.
            </p>
            <ImageGridReadOnly
              images={room.current_site_photos}
              resolveUrl={(img) => resolveImageUrl(img, "current_site")}
              testidPrefix="story-current-img"
            />
          </StorySection>
        )}

        {!empty(room.moodboards) && (
          <StorySection number="02" overline="Step two" title="Moodboards" testid="story-moodboards">
            <p className="text-sm text-neutral-500 mb-4 max-w-2xl">
              The direction — palette, texture, mood.
            </p>
            <ImageGridReadOnly
              images={room.moodboards}
              resolveUrl={(img) => resolveImageUrl(img, "moodboard")}
              testidPrefix="story-moodboard-img"
            />
          </StorySection>
        )}

        {!empty(room.reference_images) && (
          <StorySection number="03" overline="Step three" title="Reference Images" testid="story-references">
            <p className="text-sm text-neutral-500 mb-4 max-w-2xl">
              Inspiration references guiding the specification.
            </p>
            <ImageGridReadOnly
              images={room.reference_images}
              resolveUrl={(img) => resolveImageUrl(img, "reference")}
              testidPrefix="story-reference-img"
            />
          </StorySection>
        )}

        {!noOverview && (
          <StorySection number="04" overline="Step four" title="Concept Overview" testid="story-overview">
            <p
              className="font-display text-xl sm:text-2xl leading-relaxed text-neutral-800 max-w-3xl whitespace-pre-wrap"
              data-testid="story-overview-text"
            >
              {room.concept_overview}
            </p>
          </StorySection>
        )}

        {specs.length > 0 && (
          <StorySection number="05" overline="Step five" title="Material Specifications" testid="story-specs">
            <p className="text-sm text-neutral-500 mb-4 max-w-2xl">
              The surface materials that anchor this room.
            </p>
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {specs.map((r, i) => <SpecCard key={r.id || r.row_id || r.zone || i} row={r} index={i} />)}
            </div>
          </StorySection>
        )}

        {products.length > 0 && (
          <StorySection number="06" overline="Step six" title="Suggested Products" testid="story-products">
            <p className="text-sm text-neutral-500 mb-4 max-w-2xl">
              Curated products & fixtures that bring the concept to life.
            </p>
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {products.map((p, i) => <ProductCard key={p.id || i} product={p} index={i} />)}
            </div>
          </StorySection>
        )}

        {!noNotes && (
          <StorySection number="07" overline="Step seven" title="Designer Notes" testid="story-notes">
            <p
              className="text-base sm:text-lg leading-relaxed text-neutral-700 max-w-3xl whitespace-pre-wrap"
              data-testid="story-notes-text"
            >
              {room.designer_notes}
            </p>
          </StorySection>
        )}
      </div>

      <footer className="mt-20 pt-6 border-t border-black/10 flex items-center justify-between text-xs text-neutral-400">
        <div>Presented via MaterialMatch AI</div>
        {room.updated_at && (
          <div>Updated {new Date(room.updated_at).toLocaleDateString()}</div>
        )}
      </footer>
    </article>
  );
}
