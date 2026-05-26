import React from "react";
import { Sparkles } from "lucide-react";

export default function DemoModeBanner({ className = "" }) {
  return (
    <div
      className={`flex items-center gap-3 bg-black text-white rounded-2xl px-5 py-3 ${className}`}
      data-testid="demo-mode-banner"
    >
      <div className="w-7 h-7 rounded-full bg-white/10 grid place-items-center shrink-0">
        <Sparkles className="w-3.5 h-3.5" strokeWidth={1.75} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-xs font-semibold tracking-wider uppercase">Demo mode active</div>
        <div className="text-xs text-white/70 mt-0.5">
          Mock material analysis &amp; catalogue matching · Real AI integration coming next.
        </div>
      </div>
    </div>
  );
}
