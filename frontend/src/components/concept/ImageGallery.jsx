import React, { useRef, useState } from "react";
import { Upload, Trash2, ImageOff } from "lucide-react";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";

/**
 * Gallery component. Uploads images to /rooms/{roomId}/images?kind={kind},
 * lazily fetches each image data-url, and supports delete.
 *
 * Props:
 *  - roomId
 *  - kind: 'current_site' | 'moodboard' | 'reference'
 *  - images: [{ id, mime }]
 *  - onChange: (newImagesArray) => void  (parent state update)
 *  - accent: css bg class for the empty state
 *  - testidPrefix: string used to build data-testids
 */
export default function ImageGallery({
  roomId,
  kind,
  images = [],
  onChange,
  accent = "bg-neutral-50",
  testidPrefix,
}) {
  const inputRef = useRef(null);
  const [uploading, setUploading] = useState(false);
  const [urls, setUrls] = useState({}); // id -> data_url

  const loadUrl = async (img) => {
    if (urls[img.id]) return;
    try {
      const { data } = await api.get(`/rooms/${roomId}/images/${kind}/${img.id}`);
      setUrls((prev) => ({ ...prev, [img.id]: data.data_url }));
    } catch {
      /* soft-fail; card will show broken icon */
    }
  };

  React.useEffect(() => {
    images.forEach(loadUrl);
  }, [images, roomId, kind]);

  const doUpload = async (files) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      const nextImages = [...images];
      for (const file of Array.from(files)) {
        if (nextImages.length >= 12) {
          toast.error("Max 12 images per section");
          break;
        }
        const fd = new FormData();
        fd.append("file", file);
        try {
          const { data } = await api.post(
            `/rooms/${roomId}/images?kind=${kind}`,
            fd,
            { headers: { "Content-Type": "multipart/form-data" } }
          );
          nextImages.push({ id: data.id, mime: data.mime });
        } catch (e) {
          toast.error(formatApiError(e));
        }
      }
      onChange(nextImages);
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const doDelete = async (img) => {
    try {
      await api.delete(`/rooms/${roomId}/images/${kind}/${img.id}`);
      onChange(images.filter((i) => i.id !== img.id));
    } catch (e) {
      toast.error(formatApiError(e));
    }
  };

  return (
    <div className="space-y-3" data-testid={`${testidPrefix}-gallery`}>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
        {images.map((img, i) => (
          <div
            key={img.id}
            className="relative group aspect-[4/3] rounded-xl overflow-hidden border border-black/5 bg-neutral-100"
            data-testid={`${testidPrefix}-image-${i}`}
          >
            {urls[img.id] ? (
              <img src={urls[img.id]} alt="" className="w-full h-full object-cover" />
            ) : (
              <div className="w-full h-full grid place-items-center text-neutral-300">
                <ImageOff className="w-6 h-6" strokeWidth={1.5} />
              </div>
            )}
            <button
              type="button"
              onClick={() => doDelete(img)}
              className="absolute top-2 right-2 bg-black/60 backdrop-blur text-white rounded-full p-1.5 opacity-0 group-hover:opacity-100 transition-opacity"
              title="Remove"
              data-testid={`${testidPrefix}-image-delete-${i}`}
            >
              <Trash2 className="w-3.5 h-3.5" strokeWidth={1.5} />
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={uploading || images.length >= 12}
          className={`aspect-[4/3] rounded-xl border-2 border-dashed border-black/10 ${accent} hover:border-black/30 hover:bg-neutral-100 transition-all flex flex-col items-center justify-center gap-2 text-neutral-500 disabled:opacity-40`}
          data-testid={`${testidPrefix}-upload-btn`}
        >
          <Upload className="w-4 h-4" strokeWidth={1.5} />
          <span className="text-xs font-medium">
            {uploading ? "Uploading…" : images.length >= 12 ? "Max 12" : "Add photo"}
          </span>
        </button>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        multiple
        className="hidden"
        onChange={(e) => doUpload(e.target.files)}
        data-testid={`${testidPrefix}-file-input`}
      />
    </div>
  );
}
