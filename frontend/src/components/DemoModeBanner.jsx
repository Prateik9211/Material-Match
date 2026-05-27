import React from "react";
import { Sparkles, Info } from "lucide-react";
import { useConfig } from "@/lib/api";

export default function DemoModeBanner({ className = "" }) {
  const config = useConfig();
  // While config is still loading, render nothing — avoids a misleading
  // "DEMO MODE ACTIVE" flash on first render when real-AI is in fact enabled.
  if (config == null) return null;
  const analysisOn = !!config?.enable_real_analysis;
  const matchOn = !!config?.enable_real_match;

  // Both real — compact "Live AI" pill
  if (analysisOn && matchOn) {
    return (
      <div
        className={`inline-flex items-center gap-2 bg-emerald-50 border border-emerald-100 text-emerald-900 rounded-full px-3.5 py-1.5 ${className}`}
        data-testid="demo-mode-banner"
        data-mode="all-live"
      >
        <Info className="w-3.5 h-3.5 text-emerald-700" strokeWidth={1.75} />
        <span className="text-xs">
          <span className="font-semibold">Live AI</span>
          <span className="text-emerald-800/70"> · material analysis &amp; catalogue matching powered by OpenAI</span>
        </span>
      </div>
    );
  }

  // Analysis only
  if (analysisOn && !matchOn) {
    return (
      <div
        className={`inline-flex items-center gap-2 bg-[#F3F2EE] border border-black/5 text-neutral-700 rounded-full px-3.5 py-1.5 ${className}`}
        data-testid="demo-mode-banner"
        data-mode="real-analysis"
      >
        <Info className="w-3.5 h-3.5 text-neutral-500" strokeWidth={1.75} />
        <span className="text-xs">
          <span className="font-semibold text-neutral-900">AI material analysis active</span>
          <span className="text-neutral-500"> · catalogue matching still in demo mode</span>
        </span>
      </div>
    );
  }

  // Match only (unusual)
  if (!analysisOn && matchOn) {
    return (
      <div
        className={`inline-flex items-center gap-2 bg-[#F3F2EE] border border-black/5 text-neutral-700 rounded-full px-3.5 py-1.5 ${className}`}
        data-testid="demo-mode-banner"
        data-mode="real-match"
      >
        <Info className="w-3.5 h-3.5 text-neutral-500" strokeWidth={1.75} />
        <span className="text-xs">
          <span className="font-semibold text-neutral-900">Live catalogue matching</span>
          <span className="text-neutral-500"> · material analysis still in demo mode</span>
        </span>
      </div>
    );
  }

  // Both off — full demo banner
  return (
    <div
      className={`flex items-center gap-3 bg-black text-white rounded-2xl px-5 py-3 ${className}`}
      data-testid="demo-mode-banner"
      data-mode="full-demo"
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
