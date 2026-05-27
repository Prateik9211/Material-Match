// Small helpers shared across the Match sub-components.
// Kept here so the Match page itself stays focused on data flow, not presentation.

export function scoreBadgeStyle(label) {
  switch (label) {
    case "Strong Match":  return "bg-emerald-600 text-white";
    case "Good Match":    return "bg-emerald-500 text-white";
    case "Partial Match": return "bg-amber-500 text-white";
    default:              return "bg-neutral-400 text-white";
  }
}

export function attachPreview(file) {
  if (file.type?.startsWith("image/")) {
    return Object.assign(file, { preview: URL.createObjectURL(file) });
  }
  return file;
}

// MIME allow-lists used by the upload zones — single source of truth.
export const PDF_MIME_TYPES = ["application/pdf"];
export const IMG_MIME_TYPES = ["image/jpeg", "image/png", "image/webp"];
