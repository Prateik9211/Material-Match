/* Admin-only scene-segmentation debug page. Function over form.
   Not part of the product UI.
   Uploads one image, hits /api/admin/test-scene-segmentation, overlays
   Stage-A object bboxes on the image, lists per-object Stage-B material
   analysis (DNA dict from GPT-4o-mini, or deterministic shortcut, or error). */
import React, { useEffect, useRef, useState } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import api, { formatApiError } from "@/lib/api";

const DEFAULT_OBJECT_VOCAB =
  "wall, ceiling, floor, cabinet, countertop, backsplash, sofa, curtain, plant";

// Stable label -> color mapping so overlays are readable at a glance.
const COLORS = [
  "#ef4444", "#f97316", "#eab308", "#22c55e", "#06b6d4",
  "#3b82f6", "#8b5cf6", "#ec4899", "#14b8a6", "#f59e0b",
  "#84cc16", "#a855f7", "#0ea5e9", "#f43f5e",
];
const colorFor = (label) => {
  let h = 0;
  for (const c of String(label)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return COLORS[h % COLORS.length];
};

const SOURCE_BADGES = {
  dna: { text: "DNA (GPT-4o-mini)", bg: "#dbeafe", fg: "#1e40af" },
  shortcut: { text: "Shortcut (deterministic)", bg: "#dcfce7", fg: "#166534" },
  skipped: { text: "Skipped", bg: "#f3f4f6", fg: "#4b5563" },
  error: { text: "Error", bg: "#fee2e2", fg: "#991b1b" },
};

export default function AdminSceneTest() {
  const { user } = useAuth();
  const [file, setFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [objectVocab, setObjectVocab] = useState(DEFAULT_OBJECT_VOCAB);
  const [minConfidence, setMinConfidence] = useState(0.55);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [imgSize, setImgSize] = useState({ w: 0, h: 0 });
  const [expanded, setExpanded] = useState({});
  const imgRef = useRef(null);

  useEffect(() => {
    if (!file) { setPreviewUrl(null); return; }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  if (user === null) return <div style={styles.center}>Loading…</div>;
  if (!user || user.role !== "admin")
    return <Navigate to="/dashboard" replace />;

  const onFileChange = (e) => {
    const f = e.target.files?.[0] || null;
    setFile(f);
    setResult(null);
    setError(null);
    setExpanded({});
  };

  const run = async () => {
    if (!file) { setError("Choose an image first."); return; }
    setRunning(true); setError(null); setResult(null); setExpanded({});
    const fd = new FormData();
    fd.append("file", file);
    fd.append("min_confidence", String(minConfidence));
    if (objectVocab.trim()) fd.append("object_vocab", objectVocab);
    try {
      const { data } = await api.post(
        "/admin/test-scene-segmentation", fd,
        { headers: { "Content-Type": "multipart/form-data" }, timeout: 300000 },
      );
      setResult(data);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setRunning(false);
    }
  };

  const onImgLoad = () => {
    if (imgRef.current) {
      setImgSize({
        w: imgRef.current.naturalWidth,
        h: imgRef.current.naturalHeight,
      });
    }
  };

  const objects = result?.objects || [];
  const naturalW = result?.image_size?.width || imgSize.w || 1;
  const naturalH = result?.image_size?.height || imgSize.h || 1;

  return (
    <div style={styles.page}>
      <div style={styles.header}>
        <h1 style={styles.h1}>Scene Segmentation — Debug (Hybrid Pipeline)</h1>
        <p style={styles.muted}>
          Admin-only. <b>Stage A</b>: SAM3 object detection. <b>Stage B</b>:
          material classification via <code>generate_swatch_dna</code>{" "}
          (GPT-4o-mini) — same function the live matcher uses. Deterministic
          shortcuts for mirror / sink / faucet / plant. Not connected to
          analyze-region.
        </p>
      </div>

      <div style={styles.card}>
        <label style={styles.label}>
          Image
          <input type="file" accept="image/*"
                 onChange={onFileChange} data-testid="scene-test-file-input"
                 style={styles.input}/>
        </label>

        <label style={styles.label}>
          object_vocab (comma-separated)
          <input type="text" value={objectVocab}
                 onChange={(e) => setObjectVocab(e.target.value)}
                 style={styles.input} data-testid="scene-test-object-vocab"/>
        </label>

        <label style={styles.label}>
          min_confidence
          <input type="number" step="0.05" min="0" max="1"
                 value={minConfidence}
                 onChange={(e) => setMinConfidence(parseFloat(e.target.value) || 0)}
                 style={{ ...styles.input, width: 120 }}
                 data-testid="scene-test-min-confidence"/>
        </label>

        <button onClick={run} disabled={running || !file}
                style={running ? styles.buttonDisabled : styles.button}
                data-testid="scene-test-run">
          {running ? "Running…" : "Run Test"}
        </button>

        {error && (
          <div style={styles.error} data-testid="scene-test-error">
            {error}
          </div>
        )}
      </div>

      {previewUrl && (
        <div style={styles.card}>
          <div style={styles.imgWrap}>
            <img ref={imgRef} src={previewUrl} onLoad={onImgLoad}
                 alt="uploaded" style={{ width: "100%", display: "block" }}/>
            {result && naturalW > 0 && (
              <svg style={styles.svgOverlay} viewBox={`0 0 ${naturalW} ${naturalH}`}
                   preserveAspectRatio="none">
                {objects.map((o, i) => {
                  if (!o.bbox) return null;
                  const [x, y, w, h] = o.bbox;
                  const c = colorFor(o.label);
                  return (
                    <g key={i}>
                      <rect x={x} y={y} width={w} height={h}
                            fill="none" stroke={c}
                            strokeWidth={Math.max(2, naturalW / 400)}/>
                      <rect x={x} y={y - naturalH * 0.03}
                            width={Math.min(naturalW * 0.28, w)}
                            height={naturalH * 0.03}
                            fill={c} opacity="0.85"/>
                      <text x={x + naturalW * 0.005}
                            y={y - naturalH * 0.008}
                            fill="#fff"
                            fontSize={Math.max(14, naturalH * 0.02)}
                            fontFamily="monospace">
                        {`#${i + 1} ${o.label} ${(o.confidence * 100).toFixed(0)}%`}
                      </text>
                    </g>
                  );
                })}
              </svg>
            )}
          </div>
        </div>
      )}

      {result && (
        <div style={styles.card}>
          <div style={styles.summary}>
            <span><b>image:</b> {naturalW}×{naturalH}</span>
            <span><b>min_confidence:</b> {result.min_confidence}</span>
            <span><b>raw:</b> {result.objects_raw_count}</span>
            <span><b>kept:</b> {result.objects_kept_count}</span>
          </div>

          <div>
            {objects.map((o, i) => {
              const c = colorFor(o.label);
              const isOpen = !!expanded[i];
              const bbox = o.bbox
                ? o.bbox.map((v) => Math.round(v)).join(", ")
                : "—";
              const src = o.material?.source || "error";
              const badge = SOURCE_BADGES[src] || SOURCE_BADGES.error;
              const mat = o.material?.material;
              const family = mat?.material_family || "—";
              return (
                <div key={i} style={styles.objRow}
                     data-testid={`scene-test-object-${i}`}>
                  <button
                    onClick={() => setExpanded((s) => ({ ...s, [i]: !s[i] }))}
                    style={styles.objHeader}
                    data-testid={`scene-test-object-toggle-${i}`}>
                    <span style={{ ...styles.colorDot, background: c }}/>
                    <span style={styles.objLabel}>#{i + 1} · {o.label}</span>
                    <span style={styles.objConf}>{(o.confidence * 100).toFixed(1)}%</span>
                    <span style={styles.objBbox}>bbox: {bbox}</span>
                    <span
                      style={{ ...styles.badge, background: badge.bg, color: badge.fg }}>
                      {badge.text}
                    </span>
                    <span style={styles.objMatCount}>
                      {family}
                    </span>
                    <span style={styles.chev}>{isOpen ? "▾" : "▸"}</span>
                  </button>
                  {isOpen && (
                    <div style={styles.materialsPanel}>
                      <MaterialDetails material={o.material} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function MaterialDetails({ material }) {
  if (!material) return <div style={styles.muted}>No material result.</div>;
  const { source, error, crop_origin, crop_size, material: mat } = material;
  if (source === "error") {
    return <div style={styles.error}>{error || "Stage-B error"}</div>;
  }
  if (source === "skipped") {
    return (
      <div style={styles.muted}>
        Skipped — {error || "no material for this object type"}
      </div>
    );
  }
  if (!mat) {
    return <div style={styles.muted}>No DNA returned.</div>;
  }
  const pc = mat.primary_color || {};
  const secondary = Array.isArray(mat.secondary_colors) ? mat.secondary_colors : [];
  const apps = Array.isArray(mat.typical_applications) ? mat.typical_applications : [];
  return (
    <div style={styles.dnaGrid}>
      <DnaRow k="material_family" v={mat.material_family} />
      <DnaRow k="surface_type" v={mat.surface_type} />
      <DnaRow k="primary_color" v={
        <span style={styles.colorSwatch}>
          {pc.hex && (
            <span style={{ ...styles.swatchDot, background: pc.hex }} />
          )}
          <span style={{ fontFamily: "monospace" }}>
            {pc.name || "—"} {pc.hex ? `(${pc.hex})` : ""}
          </span>
        </span>
      } />
      {secondary.length > 0 && (
        <DnaRow k="secondary_colors" v={
          <span>
            {secondary.map((s, i) => (
              <span key={i} style={styles.colorSwatch}>
                {s.hex && <span style={{ ...styles.swatchDot, background: s.hex }} />}
                <span style={{ fontFamily: "monospace" }}>
                  {s.name || "—"} {s.hex ? `(${s.hex})` : ""}
                </span>
              </span>
            ))}
          </span>
        } />
      )}
      <DnaRow k="pattern" v={`${mat.pattern || "—"} (${mat.pattern_scale || "—"})`} />
      <DnaRow k="finish / gloss" v={`${mat.finish || "—"} / ${mat.gloss_level || "—"}`} />
      <DnaRow k="color_temperature" v={mat.color_temperature || "—"} />
      <DnaRow k="texture" v={mat.texture || "—"} />
      {apps.length > 0 && (
        <DnaRow k="typical_applications" v={apps.join(" · ")} />
      )}
      <DnaRow k="canonical_description" v={mat.canonical_description || "—"} />
      {crop_origin && crop_size && (
        <DnaRow k="crop"
                v={`origin ${crop_origin.join(",")} · size ${crop_size.join("x")}`} />
      )}
    </div>
  );
}

function DnaRow({ k, v }) {
  return (
    <>
      <div style={styles.dnaKey}>{k}</div>
      <div style={styles.dnaVal}>{v}</div>
    </>
  );
}

// -----------------------------------------------------------------------------
// Bare-bones inline styles — no design-system dependency; single-file component.
// -----------------------------------------------------------------------------
const styles = {
  page: {
    maxWidth: 1200, margin: "0 auto", padding: "24px 16px",
    fontFamily: "-apple-system, system-ui, sans-serif", color: "#111",
    background: "#fafafa", minHeight: "100vh",
  },
  center: {
    minHeight: "100vh", display: "grid", placeItems: "center", color: "#666",
  },
  header: { marginBottom: 16 },
  h1: { fontSize: 20, fontWeight: 700, margin: 0 },
  muted: { color: "#666", fontSize: 13, marginTop: 4 },
  card: {
    background: "#fff", border: "1px solid #e5e5e5", borderRadius: 6,
    padding: 16, marginBottom: 16,
  },
  label: {
    display: "flex", flexDirection: "column", gap: 4,
    fontSize: 13, color: "#333", marginBottom: 12,
  },
  input: {
    padding: "8px 10px", border: "1px solid #d4d4d4", borderRadius: 4,
    fontSize: 13, fontFamily: "monospace",
  },
  button: {
    background: "#111", color: "#fff", border: 0, padding: "10px 18px",
    borderRadius: 4, cursor: "pointer", fontSize: 14,
  },
  buttonDisabled: {
    background: "#999", color: "#fff", border: 0, padding: "10px 18px",
    borderRadius: 4, cursor: "not-allowed", fontSize: 14,
  },
  error: {
    background: "#fee2e2", color: "#991b1b", padding: 10, borderRadius: 4,
    fontSize: 13, marginTop: 12, whiteSpace: "pre-wrap",
  },
  imgWrap: { position: "relative", lineHeight: 0 },
  svgOverlay: {
    position: "absolute", inset: 0, width: "100%", height: "100%",
    pointerEvents: "none",
  },
  summary: {
    display: "flex", gap: 24, fontSize: 13, color: "#333",
    marginBottom: 12, flexWrap: "wrap",
  },
  objRow: { borderTop: "1px solid #eee" },
  objHeader: {
    display: "grid",
    gridTemplateColumns: "16px 1fr 60px 220px 180px 130px 20px",
    alignItems: "center", gap: 10, width: "100%",
    background: "transparent", border: 0, padding: "10px 4px",
    cursor: "pointer", textAlign: "left", fontSize: 13,
  },
  colorDot: {
    width: 12, height: 12, borderRadius: 2,
    display: "inline-block", flexShrink: 0,
  },
  objLabel: { fontFamily: "monospace" },
  objConf: { fontFamily: "monospace", color: "#333" },
  objBbox: { fontFamily: "monospace", color: "#666", fontSize: 12 },
  objMatCount: { fontFamily: "monospace", color: "#333", fontSize: 12 },
  badge: {
    fontSize: 11, padding: "2px 8px", borderRadius: 10,
    fontFamily: "monospace", textAlign: "center",
  },
  chev: { color: "#666" },
  materialsPanel: {
    background: "#f7f7f7", padding: "12px 16px", marginBottom: 8,
    borderRadius: 4,
  },
  dnaGrid: {
    display: "grid",
    gridTemplateColumns: "180px 1fr",
    gap: "6px 16px",
    fontSize: 13,
  },
  dnaKey: { color: "#666", fontFamily: "monospace" },
  dnaVal: { color: "#111" },
  colorSwatch: {
    display: "inline-flex", alignItems: "center", gap: 8, marginRight: 12,
  },
  swatchDot: {
    width: 14, height: 14, borderRadius: 3,
    border: "1px solid #d4d4d4", display: "inline-block",
  },
};
