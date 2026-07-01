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
  Pin, PinOff, Plus,
  GitFork, Github, LayoutGrid,
  User, Settings, Zap, LogOut, ChevronRight,
} from "lucide-react";
import { getToken, getUser, isAdminOrFounder } from "../../../lib/api";

const API_BASE = `${(typeof process !== "undefined" ? process.env.REACT_APP_BACKEND_URL : "") || ""}/api/aurem-dev`;

const TOOLS = [
  // Iter 212m-100 — Founder spec: Vanguard, Loop Mode, Bug Hunt removed
  // from sidebar. Vanguard & Loop now live as inline composer toggles
  // (chat-security-scan-btn + LoopModeToggle). Bug Hunt promoted to
  // the homepage marketing nav. Codebase Graph filtered for founders.
  //
  // Iter 212m-162 — Health Scanner removed from the sidebar entirely.
  // It now lives as a "Coming soon" card inside /tools (Developer
  // tools) alongside the Security Scan and Vanguard Scan cards.  The
  // underlying admin route `/codebase-health` is still alive for
  // direct navigation, just no longer surfaced as a nav link.
  //
  // Iter 212m-158 — "Developer tools" preview surface.  Visible to ALL
  // users (no adminOnly flag), routes to /tools where Health Scan,
  // Security Scan, Vanguard Scan, and Bug Hunt are listed as "Coming
  // soon" cards with notify-me.
  { id: "tools",    label: "Developer tools",   icon: LayoutGrid  },
  { id: "graph",    label: "Codebase Graph",    icon: GitFork     },
];

function Dot({ tone }) {
  // Iter 212m-125 — Live repo connection dots.
  //   • green   → backend GitHub ping returned 200
  //   • red     → backend reported disconnected (bad token, 404, network)
  //   • yellow  → check in-flight (frontend-driven, between polls)
  //   • orange  → the historical "active project" highlight, kept so
  //               older callers (e.g. tooltip-only collapsed mode)
  //               still render the right colour.
  //   • gray    → unknown / pre-first-poll.
  const isYellow = tone === "yellow";
  return (
    <span className={cn(
      "mt-[1px] size-[6px] shrink-0 rounded-full",
      tone === "orange" && "bg-primary",
      tone === "gray"   && "bg-muted-foreground/40",
      tone === "red"    && "bg-destructive",
      tone === "green"  && "bg-emerald-500",
      isYellow          && "bg-amber-400",
      isYellow          && "animate-pulse",        // breathe while checking
    )}
    data-testid={`sidebar-repo-dot-${tone || "gray"}`}
    />
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

function UserDropdown({ user, onClose, onEditProfile, onSettings, onRecharge, onLogout, isMobile = false }) {
  const ref = useRef(null);
  useEffect(() => {
    function handle(e) { if (ref.current && !ref.current.contains(e.target)) onClose(); }
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, [onClose]);

  // Iter 212m-172 — Mobile bottom-sheet variant.
  // Desktop retains the compact absolute-positioned menu.  On mobile we
  // render a full-width sheet anchored to the bottom of the viewport
  // with a backdrop, so the user gets a clear, discoverable menu with
  // Settings / Recharge / Logout (previously the mobile avatar tap
  // went straight to /settings — QA noted as P2 in iter 212m-156).
  if (isMobile) {
    return (
      <>
        {/* Backdrop */}
        <div
          data-testid="ds2-user-sheet-backdrop"
          onClick={onClose}
          className="fixed inset-0 z-[1500] bg-black/60"
          style={{ backdropFilter: "blur(2px)" }}
        />
        {/* Sheet */}
        <div
          ref={ref}
          data-testid="ds2-user-sheet"
          className="fixed inset-x-0 bottom-0 z-[1510] overflow-hidden rounded-t-xl border-t border-border bg-[#161616] shadow-[0_-8px_32px_rgba(0,0,0,0.6)]"
          style={{ animation: "ds2SheetIn 220ms cubic-bezier(0.16,1,0.3,1)" }}
        >
          <style>{`@keyframes ds2SheetIn { from { transform: translateY(100%); } to { transform: translateY(0); } }`}</style>
          {/* Handle indicator */}
          <div className="flex justify-center py-2">
            <div className="h-1 w-10 rounded-full bg-muted-foreground/30" />
          </div>
          <div className="border-b border-border px-4 pb-3">
            <p className="text-[14px] font-semibold text-foreground">
              {user?.name || user?.email || "Founder"}
            </p>
            {user?.email && (
              <p className="mt-[2px] text-[12px] text-muted-foreground">{user.email}</p>
            )}
            {user?.tier && (
              <p className="mt-1 inline-flex items-center rounded-full bg-primary/15 px-2 py-[2px] text-[10px] font-bold text-primary">
                {user.tier}
              </p>
            )}
          </div>
          <div className="py-1">
            <button data-testid="ds2-user-edit-mobile" onClick={onEditProfile}
              className="flex w-full items-center gap-3 px-4 py-3 text-[14px] text-foreground transition-colors hover:bg-secondary">
              <User className="size-4 text-muted-foreground" strokeWidth={2} /> Edit Profile
            </button>
            <button data-testid="ds2-user-settings-mobile" onClick={onSettings}
              className="flex w-full items-center gap-3 px-4 py-3 text-[14px] text-foreground transition-colors hover:bg-secondary">
              <Settings className="size-4 text-muted-foreground" strokeWidth={2} /> Settings
            </button>
            <button data-testid="ds2-user-recharge-mobile" onClick={onRecharge}
              className="flex w-full items-center gap-3 px-4 py-3 text-[14px] text-primary transition-colors hover:bg-secondary">
              <Zap className="size-4" strokeWidth={2} /> Recharge Tokens
            </button>
          </div>
          <div className="border-t border-border py-1 pb-[max(env(safe-area-inset-bottom),8px)]">
            <button data-testid="ds2-user-logout-mobile" onClick={onLogout}
              className="flex w-full items-center gap-3 px-4 py-3 text-[14px] text-destructive transition-colors hover:bg-secondary">
              <LogOut className="size-4" strokeWidth={2} /> Logout
            </button>
          </div>
        </div>
      </>
    );
  }

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
  isMobile = false,
}) {
  const isCollapsed = !pinned && collapsed;
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  // Iter 212m-125 — Live GitHub connection status per repo.
  //   liveStatus[project_id] = { status, error?, http_code? }
  // Initial state for every repo: "checking" (yellow pulsing dot) so
  // the user sees activity the moment the sidebar paints; the first
  // poll resolves within ~1 s and recolours each row.
  // Poll cadence is 30 s while the tab is visible, paused when the
  // tab is hidden (Page Visibility API) to save GitHub rate-limit
  // budget.  In-flight check is also marked "checking" so the dot
  // breathes back to yellow on every refresh.
  //
  // Iter 212m-133 — Surface the `error` reason from the backend so a
  // red dot is actionable (e.g. "repo_not_found" → "Repo deleted on
  // GitHub — click to edit/re-link").  Without this the user just
  // saw red with no path to fix.
  const [liveStatus, setLiveStatus] = useState({});
  useEffect(() => {
    if (!repos?.length) return undefined;
    let cancelled = false;
    const fetchOnce = async () => {
      // Mark all current repos as checking (yellow) before the call
      // so the user always sees movement on every poll tick.
      if (!cancelled) {
        setLiveStatus((prev) => {
          const next = { ...prev };
          for (const r of repos) {
            if (!next[r.id]) next[r.id] = { status: "checking" };
          }
          return next;
        });
      }
      try {
        const token = getToken();
        const res = await fetch(`${API_BASE}/cto/projects/connection-status`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok || cancelled) return;
        const j = await res.json();
        if (cancelled) return;
        const map = {};
        for (const s of (j.statuses || [])) {
          map[s.project_id] = {
            status: s.status === "connected" ? "connected" : "disconnected",
            error: s.error || null,
            http_code: s.http_code || null,
          };
        }
        setLiveStatus((prev) => ({ ...prev, ...map }));
      } catch {
        // Network blip — leave dots in their last state rather than
        // flicker everything to red on a transient failure.
      }
    };
    // Initial fetch, then a 30 s interval while the tab is visible.
    fetchOnce();
    let timer = null;
    const startPolling = () => {
      stopPolling();
      timer = setInterval(() => {
        if (document.visibilityState === "visible") fetchOnce();
      }, 30000);
    };
    const stopPolling = () => {
      if (timer) { clearInterval(timer); timer = null; }
    };
    const onVis = () => {
      if (document.visibilityState === "visible") {
        fetchOnce();
        startPolling();
      } else {
        stopPolling();
      }
    };
    startPolling();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      cancelled = true;
      stopPolling();
      document.removeEventListener("visibilitychange", onVis);
    };
    // Only re-run when the set of repo ids changes — not when status
    // updates (that would cause an infinite poll loop).
  }, [(repos || []).map((r) => r.id).join("|")]);

  // Iter 212m-125 — `liveStatus[id]` is now an object `{ status, error, http_code }`.
  // `liveDot` only needs the status colour; `liveError` exposes the reason for
  // the actionable inline hint we render next to red repos (iter 212m-133).
  function liveDot(repo) {
    const s = liveStatus[repo.id]?.status;
    if (s === "connected")    return "green";
    if (s === "disconnected") return "red";
    if (s === "checking")     return "yellow";
    return "gray";              // pre-first-poll (~1 s window)
  }

  function liveError(repo) {
    return liveStatus[repo.id]?.error || null;
  }

  // Iter 212m-133 — Human-friendly reason text for the tooltip on a
  // red repo. Keep these strings short so the tooltip is scannable.
  function liveReasonLabel(code) {
    switch (code) {
      case "repo_not_found":       return "Repo deleted or renamed on GitHub";
      case "invalid_token":        return "Token revoked — re-connect GitHub";
      case "missing_scope":        return "Token missing 'repo' scope";
      case "github_unauthorized":  return "GitHub rejected the auth";
      case "github_rate_limited":  return "GitHub rate-limited — wait ~60s";
      case "network_error":        return "Network glitch — will retry";
      default:                     return code ? `Disconnected (${code})` : "Disconnected";
    }
  }

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
      <div className={cn("flex h-[52px] shrink-0 items-center border-b border-sidebar-border cursor-pointer select-none",
        isCollapsed ? "justify-center" : "gap-2.5 px-4")} onClick={(e) => {
          // Iter 212m-101 — Logo click clears app caches (preserves auth)
          // then hard-reloads. Standard Cmd/Ctrl+click still navigates to
          // /dashboard without clearing (escape hatch).
          if (e.metaKey || e.ctrlKey) { navigate("/dashboard"); return; }
          try {
            const KEEP = new Set(["aurem_token", "aurem_user", "aurem_theme", "aurem_wizard_dismissed", "aurem_projects_cache", "aurem_active_project"]);
            const drops = [];
            for (let i = 0; i < localStorage.length; i++) {
              const k = localStorage.key(i);
              if (k && !KEEP.has(k)) drops.push(k);
            }
            drops.forEach((k) => { try { localStorage.removeItem(k); } catch {/*noop*/} });
            try { sessionStorage.clear(); } catch {/*noop*/}
            if ("caches" in window) {
              caches.keys().then((keys) => keys.forEach((k) => caches.delete(k))).catch(() => {/*noop*/});
            }
          } catch { /* noop */ }
          // Cache-bust the URL so the next load skips HTTP cache.
          const u = new URL(window.location.href);
          u.searchParams.set("_cb", Date.now().toString(36));
          window.location.replace(u.toString());
        }}
        title="Click to clear cache and reload (Cmd/Ctrl+click → just open dashboard)"
        data-testid="ds2-sidebar-brand"
      >
        <img
          src="https://customer-assets.emergentagent.com/job_launch-pad-237/artifacts/oj4581h8_Gemini_Generated_Image_sozbptsozbptsozb.png"
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
              // Iter 212m-133 — Surface the disconnected reason inline so a
              // red dot is actionable.  Click the "!" icon (or right-click
              // the row) to land on /projects?edit=<id> where the user can
              // re-link the repo or delete the project.
              const dot = liveDot(repo);
              const err = liveError(repo);
              const isRed = dot === "red";
              const tooltipText = isRed
                ? `${label} · ${repo.branch} · ${liveReasonLabel(err)}`
                : `${label} · ${repo.branch} · ${liveStatus[repo.id]?.status || "checking"}`;
              const goFix = (e) => {
                e?.stopPropagation();
                e?.preventDefault();
                navigate(`/projects?edit=${encodeURIComponent(repo.id)}`);
              };
              const button = (
                <button
                  data-testid={`ds2-sidebar-repo-${slug}`}
                  data-status={dot}
                  data-error={err || ""}
                  onClick={() => onSelectRepo?.(repo)}
                  onContextMenu={isRed ? goFix : undefined}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-md transition-colors",
                    repo.active
                      ? "text-sidebar-foreground"
                      : "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground",
                    isCollapsed ? "h-9 w-12 justify-center" : "px-4 py-[6px]",
                  )}>
                  <Dot tone={dot} />
                  {!isCollapsed && (
                    <div className="min-w-0 flex-1 text-left">
                      <p className="truncate text-[12px] font-medium leading-none">{label}</p>
                      <p className={cn(
                        "mt-[3px] font-mono text-[10px] leading-none truncate",
                        isRed ? "text-red-400/80" : "text-muted-foreground",
                      )}>
                        {isRed ? liveReasonLabel(err) : repo.branch}
                      </p>
                    </div>
                  )}
                  {isRed && !isCollapsed && (
                    <span
                      role="button"
                      data-testid={`ds2-sidebar-repo-${slug}-fix`}
                      onClick={goFix}
                      title="Edit project / re-link repo"
                      className="ml-1 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded
                                 border border-red-500/40 bg-red-500/10 text-red-300
                                 hover:bg-red-500/25 hover:text-red-100 transition-colors"
                    >
                      <Settings className="size-3" strokeWidth={2.5} />
                    </span>
                  )}
                </button>
              );
              return (
                <li key={repo.id || label}>
                  {isCollapsed
                    ? <Tooltip label={tooltipText}>{button}</Tooltip>
                    : (isRed
                        ? <Tooltip label={`${tooltipText} · right-click or click ⚙ to fix`}>{button}</Tooltip>
                        : button)}
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
            {TOOLS.filter((t) => {
              // Iter 212m-110 — Codebase Graph is now available to every
              // user (opens the GraphPanel drawer of their connected
              // GitHub repo via aurem:toggle-graph, no longer leaks
              // /feature-window's internal ORA map).
              //
              // Iter 212m-157 — Admin-only tools (Health Scanner) are
              // hidden from the sidebar for non-admin users.  Route
              // stays alive but the visible nav link is gone.  Admins
              // and founders see everything.
              if (t.adminOnly && !isAdminOrFounder(user)) return false;
              return true;
            }).map((tool) => {
              const Icon = tool.icon;
              // Iter 212m-162 — Health Scanner row deleted; tools row
              // active-state map now only needs to handle the dynamic
              // tools that route through /codebase-* or open drawers.
              // The remaining "tools" + "graph" entries are dispatched
              // by Dashboard.jsx::onToolClick — no path-based active
              // highlight needed for either.
              const isActive = false;
              const row = (
                <button onClick={() => onToolClick?.(tool.id)}
                  data-testid={`ds2-tool-${tool.id}`}
                  className={cn("flex w-full items-center gap-2.5 rounded-md transition-colors",
                    isActive
                      ? "bg-sidebar-accent text-sidebar-foreground"
                      : "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground",
                    isCollapsed ? "h-9 w-12 justify-center" : "px-4 py-[6px]")}>
                  <Icon className="size-3.5 shrink-0" strokeWidth={2} />
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
            onRecharge={onRecharge} onLogout={onLogout}
            isMobile={isMobile} />
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
