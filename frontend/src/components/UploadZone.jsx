import React, { useCallback, useState } from "react";
import { UploadCloud, X, FileText, Image as ImageIcon } from "lucide-react";

export default function UploadZone({
  label,
  description,
  accept = "image/*",
  multiple = false,
  onFiles,
  files = [],
  onRemove,
  testid = "upload-zone",
}) {
  const [dragging, setDragging] = useState(false);
  const inputRef = React.useRef();

  const handleDrop = useCallback(
    (e) => {
      e.preventDefault();
      setDragging(false);
      const dropped = Array.from(e.dataTransfer.files);
      if (dropped.length) onFiles(multiple ? dropped : [dropped[0]]);
    },
    [multiple, onFiles]
  );

  const handleSelect = (e) => {
    const selected = Array.from(e.target.files || []);
    if (selected.length) onFiles(multiple ? selected : [selected[0]]);
    e.target.value = "";
  };

  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between">
        <div>
          <h3 className="font-display text-lg font-semibold text-neutral-900">{label}</h3>
          {description && (
            <p className="text-sm text-neutral-500 mt-1">{description}</p>
          )}
        </div>
      </div>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        className={`cursor-pointer border-2 border-dashed rounded-2xl flex flex-col items-center justify-center p-10 min-h-[200px] transition-all ${
          dragging ? "border-black bg-[#F5F1EC]" : "border-black/10 bg-[#F5F1EC]/40 hover:bg-[#F5F1EC]"
        }`}
        data-testid={testid}
      >
        <UploadCloud className="w-8 h-8 text-neutral-500 mb-3" strokeWidth={1.25} />
        <p className="text-sm font-medium text-neutral-700">
          Drop {multiple ? "files" : "file"} here, or <span className="underline">browse</span>
        </p>
        <p className="text-xs text-neutral-400 mt-1">{accept.replaceAll(",", " · ")}</p>
        <input
          ref={inputRef}
          type="file"
          multiple={multiple}
          accept={accept}
          className="hidden"
          onChange={handleSelect}
          data-testid={`${testid}-input`}
        />
      </div>

      {files.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3" data-testid={`${testid}-previews`}>
          {files.map((f, i) => (
            <div
              key={`${f.name}-${f.size}-${i}`}
              className="relative group rounded-xl overflow-hidden bg-white border border-black/5 aspect-square"
            >
              {f.type?.startsWith("image/") && f.preview ? (
                <img src={f.preview} alt={f.name} className="w-full h-full object-cover" />
              ) : (
                <div className="w-full h-full flex flex-col items-center justify-center gap-2 p-3">
                  {f.type === "application/pdf" ? (
                    <FileText className="w-8 h-8 text-neutral-500" strokeWidth={1.25} />
                  ) : (
                    <ImageIcon className="w-8 h-8 text-neutral-500" strokeWidth={1.25} />
                  )}
                  <span className="text-xs text-neutral-600 text-center truncate w-full">
                    {f.name}
                  </span>
                </div>
              )}
              {onRemove && (
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); onRemove(i); }}
                  className="absolute top-2 right-2 w-7 h-7 rounded-full bg-white shadow-soft grid place-items-center opacity-0 group-hover:opacity-100 transition-opacity"
                  data-testid={`${testid}-remove-${i}`}
                >
                  <X className="w-3.5 h-3.5" strokeWidth={1.5} />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
