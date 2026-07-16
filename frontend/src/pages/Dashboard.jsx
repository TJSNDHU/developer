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
import RepoCleanupBanner from "../components/RepoCleanupBanner";
import PersonalTrackBanner from "../components/PersonalTrackBanner";
import FinishSetupBanner from "../components/tour/FinishSetupBanner"; // Iter 212m-200
import ConnectRepoTour from "../components/tour/ConnectRepoTour";     // Iter 212m-200
import AddLiveSiteModal from "../components/AddLiveSiteModal";        // Iter 212m-203
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

  // Iter 212m-200 — Interactive Connect-Repo tour state. Two entry
  // points: (1) FinishSetupBanner "Show me how" button, (2) email
  // deep-link with ?tour=connect-repo. Dismissed banner state is
  // per-session (sessionStorage) so we don't nag users who intend to
  // finish setup later.
  const [tourOpen, setTourOpen] = useState(false);
  const [finishBannerDismissed, setFinishBannerDismissed] = useState(() => {
    try { return sessionStorage.getItem("aurem_finish_setup_dismissed") === "1"; }
    catch { return false; }
  });
  useEffect(() => {
    if (searchParams.get("tour") === "connect-repo") {
      setTourOpen(true);
      const next = new URLSearchParams(searchParams);
      next.delete("tour");
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  // v2 chrome state ----------------------------------------------------
  const [projects,         setProjects]         = useState([]);
  const [tab,              setTab]              = useState("Chat");
  // Iter 212m-143 — Track preview window state so the topbar "Preview"
  // tab can TOGGLE: click once → open, click again → close. Previously
  // every click hard-set `open: true` so a second click was a no-op.
  // ChatPanel broadcasts `aurem:preview-state-changed` on every state
  // flip (incl. auto-open when a code reply lands), so we just mirror
  // that here as the source of truth for the button's effective state.
  const [previewOpen,      setPreviewOpen]      = useState(false);
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

  // Iter 212m-156 — Mobile drawer state.  The desktop sidebar reveal
  // logic (hover + left-edge mousemove) does NOTHING on touch
  // devices, leaving mobile users with no way to switch repos / open
  // tools / log out (caught by iter 212m-154 PROD chat E2E).  On
  // mobile (<=900 px viewport) we ignore the hover state and use
  // this explicit toggle driven by the hamburger button below.
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [isMobile,          setIsMobile]          = useState(() => {
    if (typeof window === "undefined") return false;
    try { return window.matchMedia("(max-width: 900px)").matches; }
    catch { return false; }
  });
  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const mq = window.matchMedia("(max-width: 900px)");
    const onChange = (e) => {
      setIsMobile(e.matches);
      if (!e.matches) setMobileSidebarOpen(false);   // back to desktop, close drawer
    };
    try { mq.addEventListener("change", onChange); }
    catch { mq.addListener(onChange); }
    return () => {
      try { mq.removeEventListener("change", onChange); }
      catch { mq.removeListener(onChange); }
    };
  }, []);
  // Auto-close the mobile drawer when the user picks a repo OR when
  // the underlying route changes — otherwise it stays open over the
  // chat surface and feels broken.
  const closeMobileSidebar = useCallback(() => setMobileSidebarOpen(false), []);
  // Iter 212m-124 — "edge-trigger reveal" lives separately from the
  // intent-based hover state.  When the sidebar is fully translated
  // off-screen we can't hover the panel itself; we listen for the
  // cursor crossing the left 16 px of the viewport instead.
  const [sidebarEdgeReveal, setSidebarEdgeReveal] = useState(false);
  const [chatActive,       setChatActive]       = useState(false);
  const [healthScore,      setHealthScore]      = useState(null);
  // Iter 212m-147 — Health ring UX hardening:
  //   • `healthScoreLoading` drives a "--" skeleton ring so the user
  //     never sees a flash of nothing → "0" when switching repos.
  //   • `_healthScoreCacheRef` keeps per-project last-known scores
  //     so a repo switch can show the cached value INSTANTLY while
  //     a background fetch refreshes.  Solves the "shows the previous
  //     repo's score for ~600ms" race.
  const [healthScoreLoading, setHealthScoreLoading] = useState(false);
  const _healthScoreCacheRef = useRef(new Map());
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
          position: "bottom-right",   // iter 212m-221 — avoid header overlap
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

  // Iter 212m-147 — Per-repo health score:
  //   1. Switch repo → instantly show cached score (no 0/flash).
  //   2. Show "--" skeleton if no cache (loading: true).
  //   3. Background-refetch and update cache + state.
  //   4. Stale-while-revalidate: cached score stays until fresh
  //      arrives, so the user never sees their score drop to 0
  //      mid-switch.
  useEffect(() => {
    const pid = activeProject?.project_id;
    if (!pid) { setHealthScore(null); setHealthScoreLoading(false); return; }
    // 1) Instant-show cached value if we have one.
    const cached = _healthScoreCacheRef.current.get(pid);
    if (cached !== undefined) {
      setHealthScore(cached);
      setHealthScoreLoading(false);
    } else {
      setHealthScore(null);
      setHealthScoreLoading(true);
    }
    // 2) Always refetch in the background so the cache stays warm.
    let cancelled = false;
    api.get(`/codebase-health/last?project_id=${pid}`)
      .then((r) => {
        if (cancelled) return;
        const raw = r.data?.score;
        // Distinguish "no scan yet" (null/undefined → hide ring) from
        // a real "score: 0" (legitimately critical → show red ring).
        const normalised = typeof raw === "number" ? raw : null;
        _healthScoreCacheRef.current.set(pid, normalised);
        setHealthScore(normalised);
        setHealthScoreLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setHealthScoreLoading(false);
      });
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

  // Iter 212m-204 — Listen for global "open Add Repository" event so
  // any child (PreviewPanel codebase error, banners, tour tooltip
  // CTAs) can request the wizard without prop-drilling.
  useEffect(() => {
    const onOpen = () => setShowWizard(true);
    window.addEventListener("aurem:open-add-repo", onOpen);
    return () => window.removeEventListener("aurem:open-add-repo", onOpen);
  }, []);
  const handleNewRun   = useCallback(() => {
    window.dispatchEvent(new CustomEvent("aurem:chat-session-reset"));
  }, []);
  const handleTogglePreview = useCallback(() => {
    // Iter 212m-143 — flip the state instead of hard-setting open:true
    // so consecutive clicks on the topbar Preview tab actually toggle
    // the panel open/closed (Claude-style behaviour).
    //
    // Iter 212m-203 — if the user is OPENING the preview on a project
    // that has no `preview_url` yet, intercept and show a lightweight
    // "Add your live site" modal instead of dropping them into an
    // empty iframe. On save we PATCH the project and let the effect
    // that mirrors ChatPanel state open the panel naturally.
    setPreviewOpen((cur) => {
      const next = !cur;
      const needsLiveSite =
        next
        && !!activeProject?.project_id
        && !(activeProject?.preview_url || "").trim();
      if (needsLiveSite) {
        setShowLiveSiteModal(true);
        // Don't broadcast open — modal owns the flow now.
        return cur;
      }
      window.dispatchEvent(new CustomEvent("aurem:toggle-preview", {
        detail: { open: next },
      }));
      // When closing the preview we should also re-select the Chat tab
      // so the topbar's "active" highlight follows the visible state.
      if (!next) setTab("Chat");
      return next;
    });
  }, [activeProject]);

  // Iter 212m-203 — Add-live-site modal state (per-visit). Opens the
  // first time a user clicks Preview on a project without a saved
  // `preview_url`, PATCHes the value on save, then broadcasts the
  // toggle event so the existing preview panel wiring lights up.
  const [showLiveSiteModal, setShowLiveSiteModal] = useState(false);
  const handleSaveLiveSite = useCallback(async (url) => {
    if (!activeProject?.project_id) return;
    await api.patch(`/cto/projects/${activeProject.project_id}`, { preview_url: url });
    // Optimistically reflect on the in-memory project so the iframe
    // renders immediately without waiting for the next list-fetch.
    activeProject.preview_url = url;
    setShowLiveSiteModal(false);
    setPreviewOpen(true);
    window.dispatchEvent(new CustomEvent("aurem:toggle-preview", {
      detail: { open: true },
    }));
    setTab("Preview");
  }, [activeProject]);

  // Iter 212m-143 — keep our local `previewOpen` mirror in sync with
  // ChatPanel's authoritative state (which can flip on its own when a
  // code reply lands, when a project with `preview_url` is selected,
  // or when the user hits the Hide button inside the preview panel).
  useEffect(() => {
    const onState = (e) => {
      const open = !!e?.detail?.open;
      setPreviewOpen(open);
      // If the panel closed for any reason, the Preview tab shouldn't
      // stay highlighted as "active".
      if (!open) setTab((cur) => (cur === "Preview" ? "Chat" : cur));
    };
    window.addEventListener("aurem:preview-state-changed", onState);
    return () => window.removeEventListener("aurem:preview-state-changed", onState);
  }, []);

  // Iter 212m-124 — Two sidebar states now:
  //   • sidebarCollapsed → still drives the existing narrow icon-rail
  //     mode that the in-sidebar toggle button uses (manual collapse).
  //   • sidebarFullyHidden → full translateX(-100%), used when the
  //     user is typing in chat. Reveal trigger is the left-edge
  //     mousemove listener above + sidebarPinned (explicit intent).
  // Iter 212m-156 — On mobile (<=900 px) the hover/edge-reveal logic
  // is bypassed: visibility is driven purely by `mobileSidebarOpen`
  // because touch devices can't synthesise the cursor events that
  // gate the desktop behaviour.
  const sidebarCollapsed = !isMobile
    && chatActive && !sidebarPinned && !sidebarHovered && !sidebarEdgeReveal;
  const sidebarFullyHidden = isMobile
    ? !mobileSidebarOpen
    : (chatActive && !sidebarPinned && !sidebarEdgeReveal);
  const user = getUser() || {};

  return (
    <div className="ds2-root" data-testid="dashboard-v2-root"
      data-theme={effectiveTheme}
      style={{ height: "100vh", overflow: "hidden" }}>
      {/* Iter 212m-156 — mobile drawer fade-in keyframes. */}
      <style>{`
        @keyframes ds2-fade-in {
          from { opacity: 0 }
          to   { opacity: 1 }
        }
      `}</style>
      <div style={{ display: "flex", height: "100%", width: "100%" }}>

        {/* Iter 212m-156 — Mobile hamburger.  Only visible on phones
            (<=900 px) when the drawer is closed.  Tapping it opens
            the sidebar drawer over the chat.  The fixed positioning
            keeps it above the persistent fix bar + chat composer so
            it's always reachable. */}
        {isMobile && !mobileSidebarOpen && (
          <button
            type="button"
            data-testid="mobile-sidebar-toggle"
            aria-label="Open menu"
            onClick={() => setMobileSidebarOpen(true)}
            style={{
              position: "fixed", top: 12, left: 12, zIndex: 1500,
              width: 40, height: 40, borderRadius: 10,
              background: "rgba(13,16,24,0.92)",
              border: "1px solid rgba(125,211,252,0.28)",
              color: "var(--accent-2, #7dd3fc)",
              boxShadow: "0 6px 18px rgba(0,0,0,0.55)",
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 18, fontWeight: 600,
              cursor: "pointer",
              backdropFilter: "blur(8px)",
              WebkitBackdropFilter: "blur(8px)",
            }}
          >
            ☰
          </button>
        )}

        {/* Iter 212m-156 — Mobile backdrop.  Tapping anywhere outside
            the drawer closes it.  Only renders when the drawer is
            open on a mobile viewport. */}
        {isMobile && mobileSidebarOpen && (
          <div
            data-testid="mobile-sidebar-backdrop"
            onClick={closeMobileSidebar}
            style={{
              position: "fixed", inset: 0, zIndex: 1400,
              background: "rgba(0,0,0,0.55)",
              backdropFilter: "blur(2px)",
              WebkitBackdropFilter: "blur(2px)",
              animation: "ds2-fade-in 180ms ease-out",
            }}
          />
        )}

        {/* Sidebar — v2 chrome wired to real /cto/projects/list.
            Iter 212m-124: when sidebarFullyHidden, the wrapper slides
            the panel off-screen with translateX AND collapses its
            layout slot so the chat pane reclaims the width. */}
        <div
          data-testid="ds2-sidebar-wrap"
          onMouseEnter={() => !isMobile && sidebarCollapsed && setSidebarHovered(true)}
          onMouseLeave={() => !isMobile && setSidebarHovered(false)}
          style={isMobile ? {
            // Iter 212m-156 — mobile drawer mode: float over the
            // chat surface with translateX, keep full width-280 so
            // the repo list + tool icons are readable on phones.
            position: "fixed", top: 0, left: 0, bottom: 0, zIndex: 1450,
            width: 280,
            transform: mobileSidebarOpen ? "translateX(0)" : "translateX(-100%)",
            transition: "transform 240ms cubic-bezier(.4,0,.2,1)",
            boxShadow: mobileSidebarOpen ? "8px 0 32px rgba(0,0,0,0.6)" : "none",
          } : {
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
            onSelectRepo={(...args) => {
              handleSelectRepo(...args);
              // Iter 212m-156 — auto-close the mobile drawer the
              // moment the user picks a repo, otherwise the panel
              // sits over the chat and feels broken.
              if (isMobile) closeMobileSidebar();
            }}
            onAddRepo={(...args) => {
              handleAddRepo(...args);
              if (isMobile) closeMobileSidebar();
            }}
            user={user}
            // Iter 212m-172 — pass isMobile through so the UserDropdown
            // renders the bottom-sheet variant.
            isMobile={isMobile}
            // Iter 212m-156 — every nav action (Tool click, Settings,
            // Logout, Tokens) also auto-closes the mobile drawer.
            onAfterAction={isMobile ? closeMobileSidebar : undefined}
          />
        </div>

        {/* Main column */}
        <div style={{ display: "flex", flexDirection: "column",
                      minWidth: 0, flex: 1 }}>
          <TopBar
            tab={tab}
            onTabChange={(next) => {
              // Iter 212m-143 — if user clicks the Preview tab while
              // the preview is ALREADY visible, treat that as "hide"
              // (Claude-style toggle). Otherwise: open it.
              if (next === "Preview") {
                handleTogglePreview();
                // If the preview was already open, the toggle will
                // close it and reset tab → "Chat" inside the callback.
                // Don't double-write tab here.
                if (!previewOpen) setTab("Preview");
                return;
              }
              setTab(next);
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
            breadcrumb={activeProject ? {
              owner:  activeProject.github_owner || "",
              repo:   activeProject.github_repo  || activeProject.name || "",
              branch: activeProject.branch       || "main",
            } : { owner: "", repo: "", branch: "" }}
            hasRepo={!!activeProject}
            healthScore={healthScore}
            healthScoreLoading={healthScoreLoading}
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
            {/* Iter 212m-136 — orphan-repo cleanup banner. Auto-hides
                when no projects are broken. Renders ABOVE
                ConnectRepoBanner so the user is reminded to clean up
                stale rows BEFORE we nudge them to connect a new repo. */}
            {/* Legacy users (track === null) get a one-time nudge to
                the new Personal Track. Auto-hides once they set a
                track OR dismiss it. */}
            <PersonalTrackBanner />
            <RepoCleanupBanner />
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
      {/* Iter 212m-200 — Connect-Repo interactive tour overlay. */}
      {tourOpen && <ConnectRepoTour onClose={() => setTourOpen(false)} />}

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
  onAfterAction,
  isMobile = false,
}) {
  const navigate = useNavigate();
  // Wrap navigate so the parent (Dashboard) can auto-close the mobile
  // drawer right after any nav action.
  const _go = (to) => {
    try { navigate(to); } finally { onAfterAction?.(); }
  };
  return (
    <SidebarV2Bound
      collapsed={collapsed}
      pinned={pinned}
      onPinChange={onPinChange}
      repos={repos}
      onSelectRepo={onSelectRepo}
      onAddRepo={onAddRepo}
      isMobile={isMobile}
      onToolClick={(toolId) => {
        if (toolId === "tools") _go("/tools");
        // Iter 212m-189 — Developer tools accordion sub-items route to
        // /tools/<slug> (only "live" tools dispatch this).
        else if (toolId.startsWith("tool:")) _go(`/tools/${toolId.slice(5)}`);
      }}
      user={user}
      onLogout={() => {
        try { logout(); } catch { /* ignore */ }
        _go("/login");
      }}
      onSettings={() =>     _go("/settings")}
    />
  );
}
