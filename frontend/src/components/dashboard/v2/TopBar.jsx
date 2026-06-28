/**
 * TopBar.jsx — Iter 212m-81 — JSX port of v0 `topbar.tsx`.
 */
import React from "react";
import { cn } from "./cn";
import { MessagesSquare, MonitorPlay, Workflow, ChevronRight, Zap, Gauge, Crown, Plus, Moon } from "lucide-react";

const TABS = [
  { id: "Chat",    icon: MessagesSquare },
  { id: "Preview", icon: MonitorPlay },
  { id: "Graph",   icon: Workflow },
];
const MODES = [
  { id: "swift", label: "Swift", icon: Zap },
  { id: "pro",   label: "Pro",   icon: Gauge },
  { id: "maxx",  label: "Maxx",  icon: Crown },
];

function HealthRing({ score = 87 }) {
  const r = 13;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - score / 100);
  return (
    <div className="flex items-center gap-1.5" title={`Codebase health · ${score}/100`}>
      <div className="relative size-9 shrink-0">
        <svg viewBox="0 0 36 36" className="size-9 -rotate-90">
          <circle cx="18" cy="18" r={r} fill="none" stroke="#222222" strokeWidth="3" />
          <circle cx="18" cy="18" r={r} fill="none" stroke="#FF6608" strokeWidth="3"
            strokeLinecap="round" strokeDasharray={c} strokeDashoffset={offset} />
        </svg>
        <span className="absolute inset-0 flex items-center justify-center text-[10px] font-bold tabular-nums text-foreground">{score}</span>
      </div>
      <span className="text-[9px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">Health</span>
    </div>
  );
}

export function TopBar({
  tab, onTabChange, mode, onModeChange,
  hidden = false, onNewRun,
  // Iter 212m-82 — live breadcrumb + healthScore (null → ring hidden)
  breadcrumb = { owner: "TJSNDHU", repo: "Aurem", branch: "main" },
  healthScore = null,
  // Iter 212m-89 — optional slot for the ShipStreakWidget chip
  streakSlot = null,
}) {
  return (
    <header data-testid="ds2-topbar" className={cn(
      "sticky top-0 z-20 flex flex-col border-b border-border bg-[#0A0A0A]/95 backdrop-blur-xl",
      "transition-transform duration-200 ease-in-out",
      hidden ? "-translate-y-full" : "translate-y-0",
    )}>
      <div className="flex h-[48px] items-center gap-3 px-5">
        <nav className="flex min-w-0 flex-1 items-center gap-[5px] font-mono text-[11px]">
          {breadcrumb.owner && (
            <>
              <span className="text-muted-foreground truncate">{breadcrumb.owner}/{breadcrumb.repo}</span>
              <ChevronRight className="size-3 shrink-0 text-border" strokeWidth={2} />
            </>
          )}
          <span className="truncate text-foreground">{breadcrumb.branch}</span>
        </nav>

        <div className="flex items-center gap-[2px] rounded-full border border-border bg-[#111111] p-[3px]">
          {MODES.map(({ id, label, icon: Icon }) => (
            <button key={id} onClick={() => onModeChange(id)}
              data-testid={`ds2-mode-${id}`}
              className={cn("flex items-center gap-1.5 rounded-full px-3 py-[5px] text-[11px] font-semibold transition-all duration-150",
                mode === id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}>
              <Icon className="size-[11px] shrink-0" strokeWidth={2.5} />
              {label}
            </button>
          ))}
        </div>

        {typeof healthScore === "number" && (
          <>
            <HealthRing score={healthScore} />
            <div className="h-5 w-px bg-border" />
          </>
        )}

        {streakSlot && (
          <>
            {streakSlot}
            <div className="h-5 w-px bg-border" />
          </>
        )}

        <button onClick={onNewRun} data-testid="ds2-new-run"
          className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-[6px] text-[12px] font-semibold text-primary-foreground transition-opacity hover:opacity-90 active:scale-95">
          <Plus className="size-3 shrink-0" strokeWidth={3} /> New run
        </button>

        <button aria-label="Toggle theme" className="flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground">
          <Moon className="size-[14px]" strokeWidth={2} />
        </button>
      </div>

      <div className="flex h-[38px] items-end px-5">
        {TABS.map(({ id, icon: Icon }) => (
          <button key={id} onClick={() => onTabChange(id)}
            data-testid={`ds2-tab-${id.toLowerCase()}`}
            className={cn("relative flex items-center gap-1.5 px-3 pb-[8px] text-[12px] font-medium transition-colors",
              tab === id ? "text-foreground" : "text-muted-foreground hover:text-foreground")}>
            <Icon className={cn("size-[13px] shrink-0", tab === id && "text-primary")} strokeWidth={2} />
            {id}
            {id === "Chat" && tab === "Chat" && <span className="size-[5px] shrink-0 rounded-full bg-primary" />}
            {tab === id && <span className="absolute bottom-0 left-2 right-2 h-[2px] rounded-full bg-primary" />}
          </button>
        ))}
      </div>
    </header>
  );
}
