/**
 * SidebarBound.jsx — Iter 212m-82
 *
 * Live-data variant of the v2 Sidebar. Same visual language but the
 * repository list, tools, and user dropdown are wired to real props
 * passed down from Dashboard.jsx. NO hardcoded sample data.
 */
import React, { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { cn } from "./cn";
import {
  Pin, PinOff, Plus, ShieldAlert, HeartPulse,
  RefreshCw, GitFork, Bug, Github,
  User, Settings, Zap, LogOut, ChevronRight,
} from "lucide-react";
import { getToken } from "../../../lib/api";

const API_BASE = `${(typeof process !== "undefined" ? process.env.REACT_APP_BACKEND_URL : "") || ""}/api/aurem-dev`;

const TOOLS = [
  { id: "vanguard", label: "Vanguard Security", icon: ShieldAlert },
  { id: "health",   label: "Health Scanner",    icon: HeartPulse  },
  { id: "loop",     label: "Loop Mode",         icon: RefreshCw   },
  { id: "graph",    label: "Codebase Graph",    icon: GitFork     },
  { id: "bughunt",  label: "Bug Hunt",          icon: Bug         },
];

function Dot({ tone }) {
  return (
    <span className={cn(
      "mt-[1px] size-[6px] shrink-0 rounded-full",
      tone === "orange" && "bg-primary",
      tone === "gray"   && "bg-muted-foreground/40",
      tone === "red"    && "bg-destructive",
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

function UserDropdown({ user, onClose, onEditProfile, onSettings, onRecharge, onLogout }) {
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
        <p className="text-[13px] font-semibold text-foreground">
          {user?.name || user?.email || "Founder"}
        </p>
        {user?.email && (
          <p className="mt-[2px] text-[11px] text-muted-foreground">{user.email}</p>
        )}
      </div>
      <div className="py-1">
        <button data-testid="ds2-user-edit" onClick={onEditProfile}
          className="flex w-full items-center gap-2.5 px-3.5 py-2 text-[13px] text-foreground transition-colors hover:bg-secondary">
          <User className="size-3.5 text-muted-foreground" strokeWidth={2} /> Edit Profile
        </button>
        <button data-testid="ds2-user-settings" onClick={onSettings}
          className="flex w-full items-center gap-2.5 px-3.5 py-2 text-[13px] text-foreground transition-colors hover:bg-secondary">
          <Settings className="size-3.5 text-muted-foreground" strokeWidth={2} /> Settings
        </button>
        <button data-testid="ds2-user-recharge" onClick={onRecharge}
          className="flex w-full items-center gap-2.5 px-3.5 py-2 text-[13px] text-primary transition-colors hover:bg-secondary">
          <Zap className="size-3.5" strokeWidth={2} /> Token Recharge
        </button>
      </div>
      <div className="border-t border-border py-1">
        <button data-testid="ds2-user-logout" onClick={onLogout}
          className="flex w-full items-center gap-2.5 px-3.5 py-2 text-[13px] text-destructive transition-colors hover:bg-secondary">
          <LogOut className="size-3.5" strokeWidth={2} /> Logout
        </button>
      </div>
    </div>
  );
}

export default function SidebarBound({
  collapsed = false, pinned = false, onPinChange,
  repos = [], onSelectRepo, onAddRepo,
  onToolClick, user, onEditProfile, onSettings, onRecharge, onLogout,
}) {
  const isCollapsed = !pinned && collapsed;
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  const initials = (() => {
    const name = (user?.name || user?.email || "U").trim();
    const parts = name.replace(/@.*/, "").split(/[\s._-]+/).filter(Boolean);
    return ((parts[0]?.[0] || "U") + (parts[1]?.[0] || "")).toUpperCase();
  })();

  return (
    <aside data-testid="ds2-sidebar" className={cn(
      "relative flex h-full flex-col overflow-hidden border-r border-sidebar-border bg-sidebar",
      "transition-[width] duration-200 ease-in-out",
      isCollapsed ? "w-12" : "w-[220px]",
    )}>
      {/* Brand */}
      <div className={cn("flex h-[52px] shrink-0 items-center border-b border-sidebar-border cursor-pointer",
        isCollapsed ? "justify-center" : "gap-2.5 px-4")} onClick={() => navigate("/dashboard")}
        data-testid="ds2-sidebar-brand"
      >
        <img
          src="https://customer-assets.emergentagent.com/job_launch-pad-237/artifacts/f27gnf9d_logo%20new%2011.png"
          alt="ORA by Aurem CTO"
          data-testid="ds2-sidebar-logo"
          className="size-[28px] shrink-0 rounded-full object-cover ring-1 ring-primary/25"
          draggable={false}
        />
        {!isCollapsed && (
          <>
            <div className="min-w-0 flex-1">
              <p className="text-[14px] font-bold leading-none tracking-tight text-sidebar-foreground">ORA</p>
              <p className="mt-[3px] text-[10px] leading-none text-muted-foreground">by Aurem CTO</p>
            </div>
            {onPinChange && (
              <button onClick={(e) => { e.stopPropagation(); onPinChange(!pinned); }}
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
        {/* Repositories (REAL /cto/projects/list data) */}
        <div className="mb-5">
          {!isCollapsed && (
            <p className="mb-1.5 px-4 text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
              Repositories ({repos.length})
            </p>
          )}
          <ul className="space-y-[1px]">
            {repos.length === 0 && !isCollapsed && (
              <>
                <li className="px-4 py-1 text-[11px] text-muted-foreground/70 italic">
                  No repos yet — connect in 1 click ↓
                </li>
                <li className="px-3 pt-2 pb-1">
                  <button
                    data-testid="ds2-sidebar-connect-github"
                    onClick={() => {
                      const token = getToken();
                      if (!token) return;
                      const url = `${API_BASE}/github/oauth/connect?auth=${encodeURIComponent(token)}`;
                      const w = 560, h = 720;
                      const left = Math.max(0, window.screenX + (window.outerWidth  - w) / 2);
                      const top  = Math.max(0, window.screenY + (window.outerHeight - h) / 2);
                      const popup = window.open(url, "aurem_github_oauth",
                        `width=${w},height=${h},left=${left},top=${top}`);
                      // Poll until connected, then refresh real projects via
                      // the existing event other parts of the app use.
                      const started = Date.now();
                      const iv = setInterval(async () => {
                        try {
                          const r = await fetch(`${API_BASE}/github/oauth/status`, {
                            headers: { Authorization: `Bearer ${token}` },
                          });
                          const d = await r.json();
                          if (d.connected) {
                            clearInterval(iv);
                            try { popup?.close?.(); } catch { /* xorigin */ }
                            window.dispatchEvent(new CustomEvent("aurem:projects-refresh"));
                            // ALSO open the wizard repo-picker so the user
                            // can choose which repo to actually connect.
                            onAddRepo?.();
                          }
                        } catch { /* keep polling */ }
                        if (popup?.closed || Date.now() - started > 90_000) {
                          clearInterval(iv);
                        }
                      }, 2000);
                    }}
                    className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-3 py-[7px] text-[12px] font-semibold text-primary-foreground transition-opacity hover:opacity-90 active:scale-[0.98]"
                  >
                    <Github className="size-3.5 shrink-0" strokeWidth={2.5} />
                    Connect with GitHub
                  </button>
                  <p className="mt-1.5 text-center text-[10px] text-muted-foreground/60">
                    One-click OAuth · no PAT
                  </p>
                </li>
              </>
            )}
            {repos.map((repo) => {
              const label = repo.owner ? `${repo.owner}/${repo.name}` : repo.name;
              const slug = (repo.id || repo.name || "")
                .replace(/[^a-z0-9]/gi, "-").toLowerCase();
              const button = (
                <button
                  data-testid={`ds2-sidebar-repo-${slug}`}
                  onClick={() => onSelectRepo?.(repo)}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-md transition-colors",
                    repo.active
                      ? "text-sidebar-foreground"
                      : "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground",
                    isCollapsed ? "h-9 w-12 justify-center" : "px-4 py-[6px]",
                  )}>
                  <Dot tone={repo.dot} />
                  {!isCollapsed && (
                    <div className="min-w-0 flex-1 text-left">
                      <p className="truncate text-[12px] font-medium leading-none">{label}</p>
                      <p className="mt-[3px] font-mono text-[10px] leading-none text-muted-foreground truncate">{repo.branch}</p>
                    </div>
                  )}
                </button>
              );
              return (
                <li key={repo.id || label}>
                  {isCollapsed ? <Tooltip label={`${label} · ${repo.branch}`}>{button}</Tooltip> : button}
                </li>
              );
            })}
            {!isCollapsed && (
              <li>
                <button onClick={onAddRepo}
                  data-testid="ds2-sidebar-add-repo"
                  className="flex w-full items-center gap-2 rounded-md border border-dashed border-border/50 px-4 py-[6px] text-[11px] text-muted-foreground transition-colors hover:border-border hover:text-foreground">
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
            {TOOLS.map((tool) => {
              const Icon = tool.icon;
              const isActive =
                (tool.id === "health"  && location.pathname.startsWith("/codebase-health")) ||
                (tool.id === "bughunt" && location.pathname.startsWith("/bug-hunt"))      ||
                (tool.id === "graph"   && location.pathname.startsWith("/feature-window"));
              const row = (
                <button onClick={() => onToolClick?.(tool.id)}
                  data-testid={`ds2-tool-${tool.id}`}
                  className={cn("flex w-full items-center gap-2.5 rounded-md transition-colors",
                    isActive
                      ? "bg-sidebar-accent text-sidebar-foreground"
                      : "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground",
                    isCollapsed ? "h-9 w-12 justify-center" : "px-4 py-[6px]")}>
                  <Icon className={cn("size-3.5 shrink-0", tool.id === "vanguard" && "text-destructive")} strokeWidth={2} />
                  {!isCollapsed && <span className="flex-1 text-left text-[12px]">{tool.label}</span>}
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
        {dropdownOpen && !isCollapsed && (
          <UserDropdown user={user} onClose={() => setDropdownOpen(false)}
            onEditProfile={onEditProfile} onSettings={onSettings}
            onRecharge={onRecharge} onLogout={onLogout} />
        )}
        <button onClick={() => setDropdownOpen((v) => !v)} aria-label="User menu"
          data-testid="ds2-sidebar-avatar"
          className={cn("flex items-center gap-2.5 rounded-md transition-colors hover:bg-sidebar-accent",
            isCollapsed ? "size-[26px] justify-center" : "w-full")}>
          <div className="flex size-[26px] shrink-0 items-center justify-center rounded-full bg-primary text-[10px] font-bold text-primary-foreground">{initials}</div>
          {!isCollapsed && (
            <>
              <div className="min-w-0 flex-1 text-left leading-none">
                <p className="text-[12px] font-medium text-sidebar-foreground truncate">
                  {user?.name || user?.email || "Founder"}
                </p>
                <p className="mt-[3px] inline-flex items-center rounded-full bg-primary/15 px-1.5 py-[2px] text-[9px] font-bold text-primary">
                  {user?.tier || "$9 / mo"}
                </p>
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
