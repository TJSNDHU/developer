/**
 * Dashboard.jsx — Iter 212m-82
 *
 * Real /dashboard route — REWRAPPED with the v0 design shell
 * (sidebar-changes.zip) while preserving every existing backend
 * connection: real ORA SSE chat, real Vanguard scan, real Loop mode,
 * real GitHub repo switching, real token system.
 *
 * Strategy: `<Shell requireAuth chromeless>` keeps the auth gate +
 * SessionCtx provider (so `useChatSession()` still works) but
 * skips Shell's legacy sidebar/topbar.  This file then renders the
 * v2 chrome around the SAME `<ChatPanel />` that already does all
 * the real work.
 *
 * What's WIRED to real data:
 *   • Sidebar.Repositories  → /cto/projects/list   (existing endpoint)
 *   • Sidebar repo click    → setActiveProjectId() (existing TabBar helper)
 *   • Sidebar.Tools         → tooltip-only for now; clicks route to
 *                             /codebase-health (Health), /bug-hunt (Bug Hunt)
 *   • Sidebar avatar drop   → real Edit Profile / Settings / Logout
 *                             (uses existing api.post("/auth/logout"))
 *   • TopBar breadcrumb     → {github_owner}/{github_repo} of active project
 *   • TopBar tabs           → Chat (real ChatPanel) / Preview (toggles the
 *                             existing iframe via aurem:toggle-preview)
 *                             / Graph (no-op for now — feature window WIP)
 *   • TopBar "New run"      → starts a new chat session via
 *                             window.dispatchEvent("aurem:chat-session-reset")
 *
 * What we DROPPED from the v0 mock:
 *   • Mock "ChatView" component (real ChatPanel takes its place)
 *   • Mock AskAdvisor side panel (the existing FloatingORAButton on the
 *     right is the real Ask Advisor — fires aurem:ora-open)
 *   • Mock Ship modal (real ship flow lives inside ChatPanel)
 */
import React, { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import Shell, { useChatSession } from "../components/Shell";
import ChatPanel from "../components/ChatPanel";
import {
  useActiveProject,
  setActiveProjectId as setActiveProjectIdGlobal,
} from "../components/TabBar";
import NewUserWizard, { isWizardDismissed } from "../components/NewUserWizard";
import ConnectRepoBanner from "../components/ConnectRepoBanner";
import ShipConfirmModal from "../components/ShipConfirmModal";
import ShipStreakWidget from "../components/ShipStreakWidget";
import { toast } from "../components/Toast";
import { api } from "../lib/api";
import { logout, getUser } from "../lib/api";

// v2 chrome
import { TopBar }       from "../components/dashboard/v2/TopBar";
import SidebarV2Bound   from "../components/dashboard/v2/SidebarBound";
import AskAdvisorReal   from "../components/dashboard/v2/AskAdvisorReal";

const SHARE_MILESTONES = [10, 25, 50, 100, 250];

export default function Dashboard() {
  return (
    <Shell requireAuth chromeless>
      <DashboardV2Body />
    </Shell>
  );
}

function DashboardV2Body() {
  const { sessionId, refreshSessions } = useChatSession();
  const activeProject = useActiveProject();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [showWizard, setShowWizard] = useState(false);
  const [projectCount, setProjectCount] = useState(null);

  // v2 chrome state ----------------------------------------------------
  const [projects,         setProjects]         = useState([]);
  const [tab,              setTab]              = useState("Chat");
  // Iter 212m-97 — mode pill state is now SHARED with ChatPanel via a
  // custom event bridge. Default reads from localStorage so we land on
  // the same value the chat composer sees on first paint.
  const [mode, setMode] = useState(() => {
    try { return localStorage.getItem("aurem_chat_mode") || "swift"; }
    catch { return "swift"; }
  });
  // Listen for ChatPanel broadcasts so a programmatic change there
  // (e.g. Loop mode auto-flipping swift→pro) is reflected in the pills.
  useEffect(() => {
    const onChanged = (e) => {
      const m = e?.detail?.mode;
      if (m && m !== mode) setMode(m);
    };
    window.addEventListener("aurem:chat-mode-changed", onChanged);
    return () => window.removeEventListener("aurem:chat-mode-changed", onChanged);
  }, [mode]);
  // Push pill clicks → ChatPanel
  const handleModeChange = (m) => {
    setMode(m);
    try {
      window.dispatchEvent(new CustomEvent("aurem:set-chat-mode", {
        detail: { mode: m },
      }));
    } catch { /* ignore */ }
  };
  const [sidebarPinned,    setSidebarPinned]    = useState(true);
  const [sidebarHovered,   setSidebarHovered]   = useState(false);
  const [chatActive,       setChatActive]       = useState(false);
  const [healthScore,      setHealthScore]      = useState(null);
  const [advisorCollapsed, setAdvisorCollapsed] = useState(false);

  // Iter 212m-99 — Theme cycle (dark/light/auto). The TopBar button
  // dispatches `aurem:theme-changed`; we apply `data-theme` on the
  // .ds2-root container. For "auto" we resolve to the OS preference
  // live via matchMedia.
  const [theme, setTheme] = useState(() => {
    try {
      const v = localStorage.getItem("aurem_theme");
      return ["dark", "light", "auto"].includes(v) ? v : "dark";
    } catch { return "dark"; }
  });
  useEffect(() => {
    const onChanged = (e) => {
      const t = e?.detail?.theme;
      if (["dark", "light", "auto"].includes(t)) setTheme(t);
    };
    window.addEventListener("aurem:theme-changed", onChanged);
    return () => window.removeEventListener("aurem:theme-changed", onChanged);
  }, []);
  const [systemPrefersLight, setSystemPrefersLight] = useState(() => {
    try {
      return typeof window !== "undefined"
        && window.matchMedia?.("(prefers-color-scheme: light)")?.matches === true;
    } catch { return false; }
  });
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = (e) => setSystemPrefersLight(e.matches);
    try { mq.addEventListener("change", onChange); }
    catch { mq.addListener?.(onChange); }
    return () => {
      try { mq.removeEventListener("change", onChange); }
      catch { mq.removeListener?.(onChange); }
    };
  }, []);
  const effectiveTheme = theme === "auto"
    ? (systemPrefersLight ? "light" : "dark")
    : theme;

  // ── Real /cto/projects/list load + refresh ────────────────────────
  // Iter 212m-104 — Instant render: read localStorage cache synchronously
  // (default useState value), then refresh from server in the background.
  // Eliminates the 200-800ms "no repos visible" flicker right after
  // login that made users think repos were missing.
  const PROJECTS_CACHE_KEY = "aurem_projects_cache";
  const [projectsHydrated, setProjectsHydrated] = useState(false);
  useEffect(() => {
    if (projectsHydrated) return;
    try {
      const raw = localStorage.getItem(PROJECTS_CACHE_KEY);
      if (raw) {
        const cached = JSON.parse(raw);
        if (Array.isArray(cached) && cached.length > 0) {
          setProjects(cached);
          setProjectCount(cached.length);
        }
      }
    } catch { /* corrupt cache — ignore */ }
    setProjectsHydrated(true);
  }, []);
  const reloadProjects = useCallback(() => {
    api.get("/cto/projects/list")
      .then((r) => {
        const list = (r.data?.projects || []);
        setProjects(list);
        setProjectCount(list.length);
        try { localStorage.setItem(PROJECTS_CACHE_KEY, JSON.stringify(list)); }
        catch { /* quota / private mode — ignore */ }
        if (list.length === 0 && !isWizardDismissed()) setShowWizard(true);
      })
      .catch(() => { /* silent — cached list keeps the UI populated */ });
  }, []);

  useEffect(() => {
    reloadProjects();
    window.addEventListener("aurem:projects-refresh", reloadProjects);
    return () => window.removeEventListener(
      "aurem:projects-refresh", reloadProjects,
    );
  }, [reloadProjects]);

  // Iter 212m-32 — open wizard automatically when landing on
  // /dashboard?action=connect-repo (from the onboarding nudge email).
  useEffect(() => {
    if (searchParams.get("action") === "connect-repo") {
      setShowWizard(true);
      const next = new URLSearchParams(searchParams);
      next.delete("action");
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  const openWizardFromBanner = useCallback(() => setShowWizard(true), []);
  const onWizardComplete     = useCallback(() => {
    setShowWizard(false);
    reloadProjects();
  }, [reloadProjects]);

  // Iter 145 — celebratory ship milestone toast (carried over from
  // the old Dashboard verbatim — no functional change).
  useEffect(() => {
    const handler = (e) => {
      const id = e?.detail?.task_id;
      if (!id) return;
      api.get("/wrapped/me?period=all_time").then((r) => {
        const shipped = r.data?.stats?.tasks_shipped || 0;
        const milestone = SHARE_MILESTONES.find(
          (m) => shipped >= m
            && !localStorage.getItem(`aurem_toast_${m}`),
        );
        if (!milestone) return;
        try { localStorage.setItem(`aurem_toast_${milestone}`, "1"); }
        catch { /* ignore */ }
        toast({
          message: `🎉 You've shipped ${milestone} tasks with AUREM — tap to share your Wrapped`,
          kind: "info",
          duration: 8000,
          onClick: () => navigate("/wrapped"),
        });
      }).catch(() => { /* silent */ });
    };
    window.addEventListener("aurem:shipped", handler);
    return () => window.removeEventListener("aurem:shipped", handler);
  }, [navigate]);

  // Track when ChatPanel reports streaming started/ended, so the
  // sidebar auto-collapses (v0 design's "in-chat" behaviour).
  useEffect(() => {
    const onStart = () => setChatActive(true);
    const onReset = () => setChatActive(false);
    window.addEventListener("aurem:chat-session-started", onStart);
    window.addEventListener("aurem:chat-session-reset", onReset);
    return () => {
      window.removeEventListener("aurem:chat-session-started", onStart);
      window.removeEventListener("aurem:chat-session-reset", onReset);
    };
  }, []);

  // Try to pull the real health score for the active repo. Falls back
  // silently — the ring just hides if there's no scan yet.
  useEffect(() => {
    if (!activeProject?.project_id) { setHealthScore(null); return; }
    let cancelled = false;
    api.get(`/codebase-health/last?project_id=${activeProject.project_id}`)
      .then((r) => { if (!cancelled) setHealthScore(r.data?.score ?? null); })
      .catch(() => { /* endpoint may not exist yet — silent */ });
    return () => { cancelled = true; };
  }, [activeProject?.project_id]);

  // ── Map real projects → Sidebar shape ────────────────────────────
  const repoEntries = projects.map((p) => ({
    id:     p.project_id,
    owner:  p.github_owner || "",
    name:   p.github_repo || p.name,
    branch: p.branch || "main",
    dot:    p.project_id === activeProject?.project_id ? "orange" : "gray",
    active: p.project_id === activeProject?.project_id,
    raw:    p,
  }));

  const handleSelectRepo = useCallback((repo) => {
    if (!repo?.id) return;
    setActiveProjectIdGlobal(repo.id);
  }, []);

  const handleAddRepo  = useCallback(() => setShowWizard(true), []);
  const handleNewRun   = useCallback(() => {
    window.dispatchEvent(new CustomEvent("aurem:chat-session-reset"));
  }, []);
  const handleTogglePreview = useCallback(() => {
    window.dispatchEvent(new CustomEvent("aurem:toggle-preview", {
      detail: { open: true },
    }));
  }, []);

  const sidebarCollapsed = chatActive && !sidebarPinned && !sidebarHovered;
  const user = getUser() || {};

  return (
    <div className="ds2-root" data-testid="dashboard-v2-root"
      data-theme={effectiveTheme}
      style={{ height: "100vh", overflow: "hidden" }}>
      <div style={{ display: "flex", height: "100%", width: "100%" }}>

        {/* Sidebar — v2 chrome wired to real /cto/projects/list */}
        <div
          onMouseEnter={() => sidebarCollapsed && setSidebarHovered(true)}
          onMouseLeave={() => setSidebarHovered(false)}
          style={{ flexShrink: 0 }}
        >
          <SidebarReal
            collapsed={sidebarCollapsed}
            pinned={sidebarPinned}
            onPinChange={setSidebarPinned}
            repos={repoEntries}
            onSelectRepo={handleSelectRepo}
            onAddRepo={handleAddRepo}
            user={user}
          />
        </div>

        {/* Main column */}
        <div style={{ display: "flex", flexDirection: "column",
                      minWidth: 0, flex: 1 }}>
          <TopBar
            tab={tab}
            onTabChange={(next) => {
              setTab(next);
              if (next === "Preview") handleTogglePreview();
              if (next === "Graph") {
                // Iter 212m-106 — opens the existing GraphPanel drawer
                // (force-directed nodes of the active project's repo).
                window.dispatchEvent(new CustomEvent("aurem:toggle-graph", {
                  detail: { open: true },
                }));
              }
              if (next === "Chat") {
                // Best-effort hide of the preview when returning to Chat.
                window.dispatchEvent(new CustomEvent("aurem:toggle-preview", {
                  detail: { open: false },
                }));
                window.dispatchEvent(new CustomEvent("aurem:toggle-graph", {
                  detail: { open: false },
                }));
              }
            }}
            mode={mode}
            onModeChange={handleModeChange}
            hidden={false}
            onNewRun={handleNewRun}
            breadcrumb={{
              owner:  activeProject?.github_owner || "TJSNDHU",
              repo:   activeProject?.github_repo  || activeProject?.name || "Aurem",
              branch: activeProject?.branch       || "main",
            }}
            healthScore={healthScore}
            streakSlot={<ShipStreakWidget />}
          />

          {/* Empty-state banner above ChatPanel */}
          <div style={{ flex: 1, minHeight: 0, display: "flex",
                        flexDirection: "column", overflow: "hidden" }}>
            {projectCount === 0 && (
              <ConnectRepoBanner onConnect={openWizardFromBanner} />
            )}
            <div data-testid="chat-pane"
              style={{ flex: 1, minHeight: 0, width: "100%",
                       minWidth: 0, overflow: "hidden" }}>
              <ChatPanel
                sessionId={sessionId}
                onTurnSaved={refreshSessions}
                activeProject={activeProject}
              />
            </div>
          </div>
        </div>

        {/* Real Ask Advisor — replaces legacy FloatingORAButton in chromeless mode */}
        <AskAdvisorReal
          collapsed={advisorCollapsed}
          onCollapse={setAdvisorCollapsed}
          projectId={activeProject?.project_id || null}
        />
      </div>

      {showWizard && <NewUserWizard onComplete={onWizardComplete} />}

      {/* Iter 212m-86 BUG 5 — Ship via CTO confirmation modal.
          Mounted once; opens on `aurem:open-ship-modal` event. */}
      <ShipConfirmModal />
    </div>
  );
}


/**
 * Local thin wrapper around the v2 Sidebar to inject:
 *   • Real repos (already mapped to v0 shape above)
 *   • Real avatar + dropdown menu actions
 *   • Tool routing — Vanguard → Security tab inside ChatPanel,
 *                    Health   → /codebase-health,
 *                    Bug Hunt → /bug-hunt,
 *                    Graph    → /feature-window
 */
function SidebarReal({
  collapsed, pinned, onPinChange,
  repos, onSelectRepo, onAddRepo, user,
}) {
  const navigate = useNavigate();
  // Override Sidebar's default `repositories` constant by re-importing
  // the component and feeding it through props is heavy; instead we
  // use a thin clone of the v2 Sidebar API.  Simpler: render the v2
  // Sidebar passing all live data through the props it already
  // accepts and lean on the existing `Sidebar` for visuals.
  return (
    <SidebarV2Bound
      collapsed={collapsed}
      pinned={pinned}
      onPinChange={onPinChange}
      repos={repos}
      onSelectRepo={onSelectRepo}
      onAddRepo={onAddRepo}
      onToolClick={(toolId) => {
        if (toolId === "health")   navigate("/codebase-health");
        else if (toolId === "bughunt") navigate("/bug-hunt");
        else if (toolId === "graph") navigate("/feature-window");
        else if (toolId === "vanguard") {
          // Open the existing Vanguard drawer via the well-known event.
          window.dispatchEvent(new CustomEvent("aurem:open-vanguard"));
        }
        else if (toolId === "loop") {
          window.dispatchEvent(new CustomEvent("aurem:toggle-loop"));
        }
      }}
      user={user}
      onLogout={() => {
        try { logout(); } catch { /* ignore */ }
        navigate("/login");
      }}
      onEditProfile={() => navigate("/settings")}
      onSettings={() => navigate("/settings")}
      onRecharge={() => navigate("/tokens")}
    />
  );
}


// Import the live-data variant lazily to keep the file readable. The
// component lives next to the rest of the v2 chrome.
// (top-level import added above; this inline import is removed)
