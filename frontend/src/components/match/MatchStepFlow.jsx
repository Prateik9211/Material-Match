import React from "react";

/**
 * Three-step visual flow shown above the Match controls. Purely presentational;
 * highlights which step the user is currently on.
 */
const STEPS = [
  { n: 1, label: "Upload Catalogue", sub: "PDF or product images" },
  { n: 2, label: "Run Match", sub: "Score against selected material" },
  { n: 3, label: "Review Best Matches", sub: "Top 5 with reasons" },
];

export default function MatchStepFlow({ current }) {
  return (
    <ol
      className="grid grid-cols-3 gap-2 sm:gap-4"
      data-testid="match-step-flow"
    >
      {STEPS.map((s) => {
        const active = s.n === current;
        const done = s.n < current;
        return (
          <li
            key={s.n}
            data-testid={`match-step-${s.n}`}
            data-active={active}
            data-done={done}
            className={
              "rounded-2xl px-3 sm:px-4 py-3 sm:py-4 border transition-colors " +
              (active
                ? "bg-black text-white border-black shadow-soft"
                : done
                ? "bg-emerald-50 text-emerald-900 border-emerald-100"
                : "bg-white text-neutral-500 border-black/5")
            }
          >
            <div className="flex items-center gap-2 mb-0.5">
              <span
                className={
                  "w-5 h-5 rounded-full grid place-items-center text-[10px] font-mono font-semibold " +
                  (active
                    ? "bg-white text-black"
                    : done
                    ? "bg-emerald-600 text-white"
                    : "bg-black/5 text-neutral-500")
                }
              >
                {s.n}
              </span>
              <span className="text-[10px] sm:text-xs uppercase tracking-widest font-semibold">
                Step {s.n}
              </span>
            </div>
            <div className="text-sm sm:text-base font-medium leading-tight mt-1">
              {s.label}
            </div>
            <div
              className={
                "text-[11px] mt-0.5 hidden sm:block " +
                (active ? "text-white/60" : done ? "text-emerald-700/70" : "text-neutral-400")
              }
            >
              {s.sub}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
