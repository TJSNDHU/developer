/**
 * Sidebar.jsx — Iter 212m-81 — JSX port of v0 `sidebar.tsx`.
 * Preview-only. Mounted from DashboardPreviewV2.jsx inside .ds2-root.
 */
import React, { useState, useRef, useEffect } from "react";
import { cn } from "./cn";
import { repositories, tools } from "./dashboard-data";
import {
  Pin, PinOff, Plus, ShieldAlert, HeartPulse,
  RefreshCw, GitFork, Bug,
  User, Settings, Zap, LogOut, ChevronRight,
} from "lucide-react";

const toolIcons = {
  vanguard: ShieldAlert, health: HeartPulse,
  loop: RefreshCw, graph: GitFork, bughunt: Bug,
};

function Dot({ dot }) {
  return (
    <span className={cn(
      "mt-[1px] size-[6px] shrink-0 rounded-full",
      dot === "orange" && "bg-primary",
      dot === "gray"   && "bg-muted-foreground/40",
      dot === "red"    && "bg-destructive",
    )} />
  );
}

function Tooltip({ label, children }) {
  return (
    <div className="group/tt relative flex items-center">
      {children}
      <span className="pointer-events-none absolute left-full z-50 ml-3 whitespace-nowrap rounded-md border border-border bg-popover px-2 py-1 text-[11px] text-foreground opacity-0 shadow-xl transition-opacity duration-150 group-hover/tt:opacity-100">
        {label}
      </span>
    </div>
  );
}

function UserDropdown({ onClose }) {
  const ref = useRef(null);
  useEffect(() => {
    function handle(e) { if (ref.current && !ref.current.contains(e.target)) onClose(); }
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, [onClose]);
  return (
    <div ref={ref} data-testid="ds2-user-dropdown"
      className="absolute bottom-full left-0 right-0 z-50 mb-1 overflow-hidden rounded-lg border border-border bg-[#161616] shadow-2xl">
      <div className="border-b border-border px-3.5 py-3">
        <p className="text-[13px] font-semibold text-foreground">TJ Ndukwu</p>
        <p className="mt-[2px] text-[11px] text-muted-foreground">tj@auremcto.com</p>
      </div>
      <div className="py-1">
        <button data-testid="ds2-user-edit"     className="flex w-full items-center gap-2.5 px-3.5 py-2 text-[13px] text-foreground transition-colors hover:bg-secondary">
          <User className="size-3.5 text-muted-foreground" strokeWidth={2} /> Edit Profile
        </button>
        <button data-testid="ds2-user-settings" className="flex w-full items-center gap-2.5 px-3.5 py-2 text-[13px] text-foreground transition-colors hover:bg-secondary">
          <Settings className="size-3.5 text-muted-foreground" strokeWidth={2} /> Settings
        </button>
        <button data-testid="ds2-user-recharge" className="flex w-full items-center gap-2.5 px-3.5 py-2 text-[13px] text-primary transition-colors hover:bg-secondary">
          <Zap className="size-3.5" strokeWidth={2} /> Token Recharge
        </button>
      </div>
      <div className="border-t border-border py-1">
        <button data-testid="ds2-user-logout" className="flex w-full items-center gap-2.5 px-3.5 py-2 text-[13px] text-destructive transition-colors hover:bg-secondary">
          <LogOut className="size-3.5" strokeWidth={2} /> Logout
        </button>
      </div>
    </div>
  );
}

export function Sidebar({ collapsed = false, pinned = false, onPinChange, loopOn = false, onLoopToggle }) {
  const isCollapsed = !pinned && collapsed;
  const [dropdownOpen, setDropdownOpen] = useState(false);

  return (
    <aside data-testid="ds2-sidebar" className={cn(
      "relative flex h-full flex-col overflow-hidden border-r border-sidebar-border bg-sidebar",
      "transition-[width] duration-200 ease-in-out",
      isCollapsed ? "w-12" : "w-[220px]",
    )}>
      {/* Brand */}
      <div className={cn("flex h-[52px] shrink-0 items-center border-b border-sidebar-border",
        isCollapsed ? "justify-center" : "gap-2.5 px-4")}>
        <div className="size-[26px] shrink-0 overflow-hidden rounded-full ring-1 ring-primary/25 flex items-center justify-center bg-primary text-[12px] font-bold text-primary-foreground">
          O
        </div>
        {!isCollapsed && (
          <>
            <div className="min-w-0 flex-1">
              <p className="text-[14px] font-bold leading-none tracking-tight text-sidebar-foreground">ORA</p>
              <p className="mt-[3px] text-[10px] leading-none text-muted-foreground">by Aurem CTO</p>
            </div>
            {onPinChange && (
              <button onClick={() => onPinChange(!pinned)}
                data-testid="ds2-sidebar-pin"
                aria-label={pinned ? "Unpin sidebar" : "Pin sidebar open"}
                title={pinned ? "Unpin sidebar" : "Pin sidebar open"}
                className={cn("flex size-[22px] shrink-0 items-center justify-center rounded-md transition-colors",
                  pinned ? "text-primary" : "text-muted-foreground hover:text-sidebar-foreground")}>
                {pinned ? <Pin className="size-3" strokeWidth={2.5} /> : <PinOff className="size-3" strokeWidth={2.5} />}
              </button>
            )}
          </>
        )}
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto overflow-x-hidden py-3">
        {/* Repositories */}
        <div className="mb-5">
          {!isCollapsed && (
            <p className="mb-1.5 px-4 text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">Repositories</p>
          )}
          <ul className="space-y-[1px]">
            {repositories.map((repo) => (
              <li key={repo.id}>
                {isCollapsed ? (
                  <Tooltip label={`${repo.owner ? repo.owner + "/" : ""}${repo.name} · ${repo.branch}`}>
                    <button className="flex h-9 w-12 items-center justify-center"><Dot dot={repo.dot} /></button>
                  </Tooltip>
                ) : (
                  <button className={cn("flex w-full items-center gap-2.5 rounded-md px-4 py-[6px] transition-colors",
                    repo.active ? "text-sidebar-foreground" : "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground")}>
                    <Dot dot={repo.dot} />
                    <div className="min-w-0 flex-1 text-left">
                      <p className="truncate text-[12px] font-medium leading-none">
                        {repo.owner ? `${repo.owner}/${repo.name}` : repo.name}
                      </p>
                      <p className="mt-[3px] font-mono text-[10px] leading-none text-muted-foreground">{repo.branch}</p>
                    </div>
                  </button>
                )}
              </li>
            ))}
            {!isCollapsed && (
              <li>
                <button className="flex w-full items-center gap-2 rounded-md border border-dashed border-border/50 px-4 py-[6px] text-[11px] text-muted-foreground transition-colors hover:border-border hover:text-foreground">
                  <Plus className="size-3 shrink-0" strokeWidth={2.5} /> Add Repository
                </button>
              </li>
            )}
          </ul>
        </div>

        {/* Tools */}
        <div>
          {!isCollapsed && (
            <p className="mb-1.5 px-4 text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">Tools</p>
          )}
          <ul className="space-y-[1px]">
            {tools.map((tool) => {
              const Icon = toolIcons[tool.id];
              const isLoop = tool.id === "loop";
              const row = (
                <button onClick={isLoop ? () => onLoopToggle?.(!loopOn) : undefined}
                  data-testid={`ds2-tool-${tool.id}`}
                  className={cn("flex w-full items-center gap-2.5 rounded-md transition-colors",
                    "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground",
                    isCollapsed ? "h-9 w-12 justify-center" : "px-4 py-[6px]")}>
                  <Icon className={cn("size-3.5 shrink-0", tool.id === "vanguard" && "text-destructive")} strokeWidth={2} />
                  {!isCollapsed && (
                    <>
                      <span className="flex-1 text-left text-[12px]">{tool.label}</span>
                      {tool.badge && (
                        <span className="flex h-[16px] min-w-[16px] items-center justify-center rounded-full bg-destructive px-1 text-[9px] font-bold text-white">{tool.badge}</span>
                      )}
                      {tool.score !== undefined && (
                        <span className="text-[11px] font-semibold tabular-nums text-muted-foreground">{tool.score}</span>
                      )}
                      {isLoop && (
                        <span className={cn("rounded-full px-1.5 py-[2px] text-[9px] font-bold uppercase tracking-wide transition-colors",
                          loopOn ? "bg-primary text-primary-foreground" : "bg-secondary text-muted-foreground")}>
                          {loopOn ? "ON" : "OFF"}
                        </span>
                      )}
                    </>
                  )}
                </button>
              );
              return <li key={tool.id}>{isCollapsed ? <Tooltip label={tool.label}>{row}</Tooltip> : row}</li>;
            })}
          </ul>
        </div>
      </div>

      {/* User footer */}
      <div className={cn("relative shrink-0 border-t border-sidebar-border",
        isCollapsed ? "flex justify-center py-3" : "flex items-center gap-2.5 px-4 py-3")}>
        {dropdownOpen && !isCollapsed && <UserDropdown onClose={() => setDropdownOpen(false)} />}
        <button onClick={() => setDropdownOpen((v) => !v)} aria-label="User menu"
          data-testid="ds2-sidebar-avatar"
          className={cn("flex items-center gap-2.5 rounded-md transition-colors hover:bg-sidebar-accent",
            isCollapsed ? "size-[26px] justify-center" : "w-full")}>
          <div className="flex size-[26px] shrink-0 items-center justify-center rounded-full bg-primary text-[10px] font-bold text-primary-foreground">TJ</div>
          {!isCollapsed && (
            <>
              <div className="min-w-0 flex-1 text-left leading-none">
                <p className="text-[12px] font-medium text-sidebar-foreground">Founder</p>
                <p className="mt-[3px] inline-flex items-center rounded-full bg-primary/15 px-1.5 py-[2px] text-[9px] font-bold text-primary">$9 / mo</p>
              </div>
              <ChevronRight className={cn("size-3 shrink-0 text-muted-foreground transition-transform duration-150",
                dropdownOpen && "-rotate-90")} strokeWidth={2.5} />
            </>
          )}
        </button>
      </div>
    </aside>
  );
}
