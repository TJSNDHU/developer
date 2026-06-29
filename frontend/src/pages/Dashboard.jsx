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
import React, { useCallback, useEffect, useRef, useState } from "react";
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
import SecretScanCard from "../components/SecretScanCard";
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
  const [sidebarPinned,    setSidebarPinned]    = useState(false);
  const [sidebarHovered,   setSidebarHovered]   = useState(false);
  // Iter 212m-124 — "edge-trigger reveal" lives separately from the
  // intent-based hover state.  When the sidebar is fully translated
  // off-screen we can't hover the panel itself; we listen for the
  // cursor crossing the left 16 px of the viewport instead.
  const [sidebarEdgeReveal, setSidebarEdgeReveal] = useState(false);
  const [chatActive,       setChatActive]       = useState(false);
  const [healthScore,      setHealthScore]      = useState(null);
  const [advisorCollapsed, setAdvisorCollapsed] = useState(false);

  // Iter 212m-124 — Left-edge reveal trigger.  Per founder spec:
  // once the sidebar fully hides (chatActive=true), the ONLY way to
  // bring it back without explicit pinning is to move the cursor
  // into the leftmost 16 px of the viewport.  Mouse leaving the
  // sidebar area (clientX > sidebar width) collapses it again so
  // the chat pane reclaims the full width.
  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const SIDEBAR_PX = 240;       // width of the expanded sidebar
    const onMove = (e) => {
      const x = e.clientX;
      if (x <= 16) {
        if (!sidebarEdgeReveal) setSidebarEdgeReveal(true);
      } else if (x > SIDEBAR_PX + 24 && sidebarEdgeReveal) {
        // Mouse drifted off the sidebar — re-hide.
        setSidebarEdgeReveal(false);
      }
    };
    window.addEventListener("mousemove", onMove, { passive: true });
    return () => window.removeEventListener("mousemove", onMove);
  }, [sidebarEdgeReveal]);

  // Iter 212m-111 — Theme is permanently locked to NIGHT (dark). The
  // user-facing day/night toggle has been removed per founder spec.
  // The data-theme attribute below is hard-coded to "dark" so any
  // CSS variable scoped to [data-theme="dark"] resolves correctly.
  const effectiveTheme = "dark";

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
  // Iter 212m-111 — Focus Mode: any user activity inside the chat
  // pane (typing in composer, scrolling history, clicking inside
  // chat) also flips chatActive=true so the sidebar / topbar / Ask
  // Advisor all fade away. They reappear on hover near their
  // respective edges (handled in their own mousemove listeners).
  useEffect(() => {
    const onStart = () => setChatActive(true);
    const onReset = () => setChatActive(false);
    const onFocus = () => setChatActive(true);
    window.addEventListener("aurem:chat-session-started", onStart);
    window.addEventListener("aurem:chat-session-reset", onReset);
    window.addEventListener("aurem:chat-focus", onFocus);
    return () => {
      window.removeEventListener("aurem:chat-session-started", onStart);
      window.removeEventListener("aurem:chat-session-reset", onReset);
      window.removeEventListener("aurem:chat-focus", onFocus);
    };
  }, []);

  // Iter 212m-111 — Auto-collapse Ask Advisor on chat focus; expand
  // again when the cursor lands near the right edge (last 32 px). The
  // existing AskAdvisor toggle button keeps working — this just adds
  // a hover-reveal complement so users don't have to chase the toggle.
  // Iter 212m-112 — Effect deps are intentionally limited to
  // `chatActive` (not `advisorCollapsed`) so the hover-reveal path on
  // the mousemove listener below doesn't get instantly reverted by
  // this effect re-firing on advisorCollapsed flips.
  const advisorAutoRef = useRef(false);
  useEffect(() => {
    if (chatActive) {
      // Auto-collapse ONCE per chatActive=true transition.
      if (!advisorAutoRef.current) {
        advisorAutoRef.current = true;
        setAdvisorCollapsed(true);
      }
    } else if (advisorAutoRef.current) {
      // Iter 212m-122 — Per founder spec: do NOT auto-re-expand the
      // Ask Advisor when the chat becomes inactive. Once collapsed
      // (by either focus mode or the user's manual click), the ONLY
      // way to bring it back is to click the small ADVISOR toggle.
      // We still flip the ref so the next chatActive=true transition
      // can collapse again if the user re-opened it manually.
      advisorAutoRef.current = false;
    }
  }, [chatActive]);
  // Iter 212m-122 — Per founder spec: once Ask Advisor auto-collapses
  // (chat focus mode), the ONLY way to re-open it is to click the
  // small "ADVISOR" toggle button. No hover-reveal, no edge-trigger.
  // The previous mousemove listener that auto-expanded the panel
  // when the cursor entered the last 32 px of the right edge has
  // been intentionally removed — it surprised the user mid-typing
  // and broke the focus-mode contract.

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

  // Iter 212m-124 — Two sidebar states now:
  //   • sidebarCollapsed → still drives the existing narrow icon-rail
  //     mode that the in-sidebar toggle button uses (manual collapse).
  //   • sidebarFullyHidden → full translateX(-100%), used when the
  //     user is typing in chat. Reveal trigger is the left-edge
  //     mousemove listener above + sidebarPinned (explicit intent).
  const sidebarCollapsed = chatActive && !sidebarPinned && !sidebarHovered && !sidebarEdgeReveal;
  const sidebarFullyHidden = chatActive && !sidebarPinned && !sidebarEdgeReveal;
  const user = getUser() || {};

  return (
    <div className="ds2-root" data-testid="dashboard-v2-root"
      data-theme={effectiveTheme}
      style={{ height: "100vh", overflow: "hidden" }}>
      <div style={{ display: "flex", height: "100%", width: "100%" }}>

        {/* Sidebar — v2 chrome wired to real /cto/projects/list.
            Iter 212m-124: when sidebarFullyHidden, the wrapper slides
            the panel off-screen with translateX AND collapses its
            layout slot so the chat pane reclaims the width. */}
        <div
          data-testid="ds2-sidebar-wrap"
          onMouseEnter={() => sidebarCollapsed && setSidebarHovered(true)}
          onMouseLeave={() => setSidebarHovered(false)}
          style={{
            flexShrink: 0,
            transform: sidebarFullyHidden ? "translateX(-100%)" : "translateX(0)",
            width: sidebarFullyHidden ? 0 : "auto",
            overflow: "hidden",
            transition: "transform 220ms ease-in-out, width 220ms ease-in-out",
          }}
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
            streakSlot={
              <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                <ShipStreakWidget />
                <SecretScanCard
                  variant="dashboard"
                  repoOwner={activeProject?.github_owner}
                  repoName={activeProject?.github_repo}
                />
              </span>
            }
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
        else if (toolId === "graph") {
          // Iter 212m-110 — sidebar Codebase Graph now opens the
          // GraphPanel drawer (user's connected GitHub repo) instead
          // of /feature-window which exposes ORA's internal feature
          // map. The drawer is mounted in ChatPanel and listens to
          // the `aurem:toggle-graph` event.
          window.dispatchEvent(new CustomEvent("aurem:toggle-graph", {
            detail: { open: true },
          }));
        }
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
