/**
 * Shell.jsx — Application chrome (sidebar + topbar) shared across pages.
 *
 * Sidebar contains brand, nav, "Recent Chats" section (when authenticated),
 * user card, and the api-online health pill.
 *
 * Chat session selection is exposed via Context — Dashboard subscribes,
 * other pages just consume the chrome.
 */
import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, Rocket, Database, Globe, Settings as Cog,
  Coins, BarChart3, LogOut, Zap, MessageSquare, Plus, Trash2,
  ChevronsLeft, ChevronsRight, FolderGit2, Menu, X, Trophy, Gift,
} from "lucide-react";
import { api, getUser, getToken, logout, healthApi, newSessionId, setUser as saveUser } from "../lib/api";
import { clearUICacheAndReload } from "../lib/cacheCleaner";
import { toast } from "./Toast";
import TokenBell from "./TokenBell";
import PWAInstallPrompt from "./PWAInstallPrompt";
import FloatingORAButton from "./FloatingORAButton";
import ClearCacheButton from "./ClearCacheButton";
import ThemeToggle from "./ThemeToggle";

const NAV = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard, testid: "nav-dashboard" },
  { to: "/projects", label: "Projects", icon: FolderGit2, testid: "nav-projects" },
  { to: "/deploy", label: "Deploy", icon: Rocket, testid: "nav-deploy" },
  { to: "/database", label: "Database", icon: Database, testid: "nav-database" },
  { to: "/domain", label: "Domain", icon: Globe, testid: "nav-domain" },
  { to: "/tokens", label: "Tokens", icon: Coins, testid: "nav-tokens" },
  { to: "/analytics", label: "Analytics", icon: BarChart3, testid: "nav-analytics" },
  { to: "/wall", label: "Ship Wall", icon: Trophy, testid: "nav-wall" },
  { to: "/wrapped", label: "Wrapped", icon: Gift, testid: "nav-wrapped" },
  { to: "/automations", label: "Automations", icon: Zap, testid: "nav-automations" },
  { to: "/settings", label: "Settings", icon: Cog, testid: "nav-settings" },
];

const SESSION_KEY = "aurem_active_session";
const COLLAPSED_KEY = "aurem_sidebar_collapsed";

// ── Context ────────────────────────────────────────────────────────────
const SessionCtx = createContext({
  sessionId: null,
  setSessionId: () => {},
  refreshSessions: () => {},
  tokensRemaining: null,
  setTokensRemaining: () => {},
  refreshTokens: () => {},
});

export function useChatSession() {
  return useContext(SessionCtx);
}

// ── Shell ──────────────────────────────────────────────────────────────
export default function Shell({ children, requireAuth, chromeless = false }) {
  const navigate = useNavigate();
  const location = useLocation();
  const user = getUser();
  const token = getToken();
  const [health, setHealth] = useState({ ok: true, _initial: true });
  const [sessionId, setSessionIdState] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [collapsed, setCollapsedState] = useState(() => {
    // Iter 146 — default is collapsed (compact). Expanded only when
    // user explicitly toggles via the sidebar control.
    const v = localStorage.getItem(COLLAPSED_KEY);
    return v === null ? true : v === "1";
  });

  // Iter 146 — auto-hide on typing.
  // When the user starts typing in ChatPanel, the sidebar slides
  // fully off-screen and a thin hot-zone strip appears on the left
  // edge. Hovering that strip (desktop) or tapping the mobile menu
  // pill brings the sidebar back. A second `aurem:chat-typing-stopped`
  // event (fired on textarea blur with empty content) re-arms it.
  const [hiddenForTyping, setHiddenForTyping] = useState(false);
  const peekFromHoverRef = useRef(false);

  // ── Active project (synced across tabs/components) ────────────────
  const [activeProjectId, setActiveProjectId] = useState(() =>
    localStorage.getItem("aurem_active_project") || null
  );
  useEffect(() => {
    const onChange = () =>
      setActiveProjectId(localStorage.getItem("aurem_active_project") || null);
    window.addEventListener("aurem:project-changed", onChange);
    return () => window.removeEventListener("aurem:project-changed", onChange);
  }, []);

  const sessionKeyFor = useCallback(
    (pid) => (pid ? `aurem_session_proj_${pid}` : "aurem_session_home"),
    []
  );

  // When the active project changes (or on first mount), switch session.
  //
  // Iter 170 — Cross-device chat sync.
  // Previously: if localStorage had no sessionId for this project, we
  // immediately minted a brand-new one. That meant a user logging in
  // on mobile (empty localStorage) got a blank chat even though their
  // desktop had a fully-populated session on the server.
  //
  // Now: if localStorage is empty, we async-fetch the most-recent
  // server-side session for this project (scoped to the user via the
  // auth token) and adopt it. Only mint a fresh id if the server has
  // none. The localStorage cache stays authoritative for same-device
  // continuity once it's populated.
  useEffect(() => {
    const key = sessionKeyFor(activeProjectId);
    const cached = localStorage.getItem(key);
    if (cached) {
      setSessionIdState(cached);
      return;
    }
    let cancelled = false;
    (async () => {
      let adopted = null;
      if (token) {
        try {
          const params = activeProjectId
            ? { project_id: activeProjectId }
            : { project_id: "home" };
          const r = await api.get("/chat/sessions", { params });
          const list = r.data?.sessions || [];
          if (list.length && list[0]?.session_id) {
            adopted = list[0].session_id;
          }
        } catch { /* fall through to fresh id */ }
      }
      if (cancelled) return;
      const next = adopted || newSessionId();
      localStorage.setItem(key, next);
      setSessionIdState(next);
    })();
    return () => { cancelled = true; };
  }, [activeProjectId, sessionKeyFor, token]);

  const toggleCollapsed = useCallback(() => {
    setCollapsedState((c) => {
      const next = !c;
      localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0");
      return next;
    });
  }, []);

  // ── Iter 64 — Mobile drawer state ──────────────────────────────────
  // On <=900px viewports the sidebar becomes an off-canvas drawer. The
  // grid template + transform are owned by CSS in index.css; we just
  // toggle the `data-drawer-open` attribute and render the menu button.
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined"
      && window.matchMedia("(max-width: 900px)").matches
  );
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 900px)");
    const onChange = (e) => {
      setIsMobile(e.matches);
      if (!e.matches) setDrawerOpen(false);   // back to desktop, close drawer
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  // Close drawer whenever route changes (mobile UX expectation)
  useEffect(() => { setDrawerOpen(false); }, [location.pathname]);

  // Iter 146 — typing-hidden state, hot-zone, default collapsed (above).
  // Iter 151 — track ORA panel open state so the main app grid can
  // shrink and let the panel sit beside the chat (split-screen), not
  // overlay it. FloatingORAButton dispatches the event.
  const [oraOpen, setOraOpen] = useState(false);
  useEffect(() => {
    const onState = (e) => setOraOpen(!!e?.detail?.open);
    window.addEventListener("aurem:ora-panel-state", onState);
    return () => window.removeEventListener("aurem:ora-panel-state", onState);
  }, []);
  // Fires once the user sends their first message in a chat session,
  // and stays hidden until they switch sessions (new chat, project
  // change) or hover the left-edge hot-zone. This matches the
  // sovereign-CTO pattern: focus first, navigation on demand.
  useEffect(() => {
    const onStart = () => {
      setHiddenForTyping(true);
    };
    const onReset = () => {
      setHiddenForTyping(false);
      peekFromHoverRef.current = false;
    };
    window.addEventListener("aurem:chat-session-started", onStart);
    window.addEventListener("aurem:chat-session-reset", onReset);
    return () => {
      window.removeEventListener("aurem:chat-session-started", onStart);
      window.removeEventListener("aurem:chat-session-reset", onReset);
    };
  }, []);

  // Bring sidebar back when cursor hits the left hot-zone (desktop).
  const onHotZoneEnter = useCallback(() => {
    if (isMobile) return;
    peekFromHoverRef.current = true;
    setHiddenForTyping(false);
  }, [isMobile]);
  // When the cursor leaves the sidebar after a hover-peek, slide it
  // back away so the chat reclaims the full width again. We only
  // re-hide if this was a hover-peek (user didn't manually toggle).
  const onSidebarMouseLeave = useCallback(() => {
    if (isMobile) return;
    if (peekFromHoverRef.current) {
      peekFromHoverRef.current = false;
      setHiddenForTyping(true);
    }
  }, [isMobile]);

  // ── Token wallet polling ───────────────────────────────────────────
  // Iter 212m-15 — `is_unlimited` short-circuit. Founder / Team-plan
  // accounts return `is_unlimited=true` with effective_limit ~1e12; the
  // raw `tokens_remaining` field can still surface as negative (from
  // `tokens_granted - used` math on the server side) which used to
  // make TokenBell scream 'Almost out — recharge' at users who had
  // an unlimited quota. Track the flag separately and pass it down so
  // the bell can short-circuit BEFORE any threshold math runs.
  const [tokensRemaining, setTokensRemaining] = useState(
    () => (getUser() || {}).tokens_remaining ?? null
  );
  const [tokensUnlimited, setTokensUnlimited] = useState(
    () => !!((getUser() || {}).is_unlimited)
  );

  const refreshTokens = useCallback(async () => {
    if (!token) return;
    try {
      // Prefer the richer /usage/me endpoint which returns is_unlimited /
      // is_exhausted / effective_limit. Fall back to /auth/tokens (older)
      // for backward compat if /usage/me 404s (rare; only on very old
      // backends pre-iter 80).
      let used = null;
      let unlimited = false;
      try {
        const r = await api.get("/usage/me");
        used = r.data?.remaining;
        unlimited = !!r.data?.is_unlimited;
      } catch {
        const r2 = await api.get("/auth/tokens");
        used = r2.data?.tokens_remaining;
      }
      if (typeof used === "number") {
        setTokensRemaining(used);
        setTokensUnlimited(unlimited);
        const u = getUser();
        if (u) saveUser({ ...u, tokens_remaining: used, is_unlimited: unlimited });
      }
    } catch { /* ignore */ }
  }, [token]);

  useEffect(() => {
    if (!token) return;
    refreshTokens();
    const id = setInterval(refreshTokens, 30000);
    return () => clearInterval(id);
  }, [token, refreshTokens]);

  useEffect(() => {
    if (requireAuth && !token) navigate("/login", { replace: true });
  }, [requireAuth, token, navigate]);

  useEffect(() => {
    healthApi
      .get("/health")
      .then((r) => setHealth(r.data))
      .catch(() => setHealth({ ok: false }));
  }, []);

  const setSessionId = useCallback((id) => {
    setSessionIdState(id);
    const key = sessionKeyFor(activeProjectId);
    if (id) localStorage.setItem(key, id);
    else localStorage.removeItem(key);
  }, [activeProjectId]);

  const refreshSessions = useCallback(async () => {
    if (!token) return;
    try {
      const params = activeProjectId
        ? { project_id: activeProjectId }
        : { project_id: "home" };
      const r = await api.get("/chat/sessions", { params });
      setSessions(r.data?.sessions || []);
    } catch {
      /* ignore */
    }
  }, [token, activeProjectId]);

  useEffect(() => {
    if (token) refreshSessions();
  }, [token, refreshSessions]);

  // Re-fetch sidebar when project changes
  useEffect(() => {
    if (token) refreshSessions();
  }, [activeProjectId, token, refreshSessions]);

  const startNewSession = useCallback(() => {
    const id = newSessionId();
    setSessionId(id);
    navigate("/dashboard");
  }, [navigate, setSessionId]);

  const openSession = useCallback(
    (id) => {
      setSessionId(id);
      navigate("/dashboard");
    },
    [navigate, setSessionId]
  );

  const deleteSession = useCallback(
    async (e, id) => {
      e.stopPropagation();
      try {
        await api.delete(`/chat/sessions/${id}`);
        if (id === sessionId) {
          const next = newSessionId();
          setSessionId(next);
        }
        refreshSessions();
      } catch {
        /* ignore */
      }
    },
    [sessionId, setSessionId, refreshSessions]
  );

  // Toggle the network-mesh glass background on body for authenticated
  // shell pages. Login / Landing render outside of Shell so they keep
  // the original solid amber gradient.
  useEffect(() => {
    document.body.classList.add("aurem-glass");
    return () => { document.body.classList.remove("aurem-glass"); };
  }, []);

  return (
    <SessionCtx.Provider value={{
      sessionId, setSessionId, refreshSessions,
      tokensRemaining, setTokensRemaining, refreshTokens,
    }}>
      {/* Iter 212m-82 — `chromeless` escape hatch.  When set, Shell
          still runs the auth gate + provides SessionCtx, but skips
          its legacy sidebar/topbar so a new page (DashboardV2) can
          render its own chrome around the same {children}. */}
      {chromeless ? <>{children}</> : (
      <div
        className="aurem-app-shell"
        data-collapsed={collapsed ? "true" : "false"}
        data-drawer-open={drawerOpen ? "true" : "false"}
        data-typing-hidden={hiddenForTyping && !drawerOpen ? "true" : "false"}
        data-ora-open={oraOpen && !isMobile ? "true" : "false"}
        style={{
          minHeight: "100vh",
          display: "grid",
          transition: "grid-template-columns 240ms cubic-bezier(0.4, 0, 0.2, 1)",
        }}
      >
        {/* Iter 146 — desktop hot-zone strip. Visible only when the
            sidebar has slid off after the user started typing. Hovering
            brings the sidebar back; clicking is also accepted. */}
        {!isMobile && hiddenForTyping && (
          <div
            data-testid="sidebar-hotzone"
            className="aurem-side-hotzone"
            onMouseEnter={onHotZoneEnter}
            onClick={onHotZoneEnter}
            title="Show sidebar"
          />
        )}
        {/* Mobile hamburger — visible only <=900px via CSS */}
        <button
          type="button"
          className="aurem-mobile-menu-btn"
          data-testid="mobile-menu-btn"
          aria-label={drawerOpen ? "Close menu" : "Open menu"}
          onClick={() => setDrawerOpen((v) => !v)}
        >
          {drawerOpen ? <X size={18} /> : <Menu size={18} />}
        </button>

        {/* Backdrop — only when drawer is open on mobile */}
        {isMobile && drawerOpen && (
          <div
            className="aurem-mobile-backdrop"
            data-testid="mobile-backdrop"
            onClick={() => setDrawerOpen(false)}
          />
        )}
        <aside
          data-testid="app-sidebar"
          data-collapsed={collapsed ? "true" : "false"}
          className="glass-sidebar"
          onMouseLeave={onSidebarMouseLeave}
          style={{
            borderRight: "1px solid var(--border)",
            padding: collapsed ? "28px 10px" : "28px 16px",
            display: "flex",
            flexDirection: "column",
            gap: 6,
            position: "sticky",
            top: 0,
            height: "100vh",
            overflow: "hidden",
            transition: "padding 240ms",
          }}
        >
          {/* Brand row + collapse toggle */}
          <div
            style={{
              marginBottom: 24,
              display: "flex",
              alignItems: "center",
              justifyContent: collapsed ? "center" : "space-between",
              gap: 8,
            }}
          >
            <button
              type="button"
              onClick={async (e) => {
                e.preventDefault();
                // Iter 212m-25 — Logo click = clear UI cache + auto
                // refresh the CURRENT page (login stays). User asked
                // for this so cached UI state never sticks to a
                // stale render after a deploy or weird transient.
                try {
                  const { cleared } = await clearUICacheAndReload();
                  const total = (cleared.localStorage || 0)
                                + (cleared.sessionStorage || 0)
                                + (cleared.indexedDB || 0)
                                + (cleared.caches || 0);
                  toast({
                    message: `🧹 Cache cleared (${total} item${total === 1 ? "" : "s"}) — refreshing…`,
                    kind: "success",
                  });
                } catch (err) {
                  toast({
                    message: `Cache clear failed: ${err.message || err}`,
                    kind: "error",
                  });
                }
              }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                paddingLeft: collapsed ? 0 : 6,
                background: "none",
                border: "none",
                cursor: "pointer",
                color: "inherit",
                fontFamily: "inherit",
                textAlign: "left",
              }}
              data-testid="brand-link"
              title="Click to clear cache and refresh (you stay signed in)"
            >
              <Zap size={20} style={{ color: "var(--accent)", flexShrink: 0 }} />
              {!collapsed && (
                <div>
                  <span
                    className="serif"
                    style={{ fontSize: 18, color: "var(--text)" }}
                  >
                    AUREM Dev
                  </span>
                  <span
                    className="eyebrow"
                    style={{
                      fontSize: 9,
                      marginTop: 4,
                      display: "block",
                    }}
                  >
                    sovereign cto
                  </span>
                </div>
              )}
            </button>
            {!collapsed && (
              <button
                data-testid="sidebar-collapse-btn"
                onClick={toggleCollapsed}
                title="Collapse sidebar"
                style={{
                  background: "none",
                  border: "1px solid var(--border)",
                  color: "var(--text-faint)",
                  width: 24,
                  height: 24,
                  borderRadius: 4,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                  transition: "color 120ms, border-color 120ms",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.color = "var(--accent-2)";
                  e.currentTarget.style.borderColor = "var(--border-strong)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.color = "var(--text-faint)";
                  e.currentTarget.style.borderColor = "var(--border)";
                }}
              >
                <ChevronsLeft size={13} />
              </button>
            )}
          </div>

          {/* Iter 212m-25 — Clear-cache button right under the brand
              (hidden when the sidebar is collapsed). */}
          <ClearCacheButton collapsed={collapsed} testid="clear-cache-btn" />

          {/* Expand button when collapsed (placed below brand for tap target) */}
          {collapsed && (
            <button
              data-testid="sidebar-expand-btn"
              onClick={toggleCollapsed}
              title="Expand sidebar"
              style={{
                background: "none",
                border: "1px solid var(--border)",
                color: "var(--text-faint)",
                width: "100%",
                height: 28,
                borderRadius: 4,
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                marginBottom: 12,
                transition: "color 120ms, border-color 120ms",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = "var(--accent-2)";
                e.currentTarget.style.borderColor = "var(--border-strong)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = "var(--text-faint)";
                e.currentTarget.style.borderColor = "var(--border)";
              }}
            >
              <ChevronsRight size={13} />
            </button>
          )}

          {token &&
            NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                data-testid={n.testid}
                title={collapsed ? n.label : undefined}
                style={({ isActive }) => ({
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  padding: collapsed ? "10px 0" : "9px 12px",
                  justifyContent: collapsed ? "center" : "flex-start",
                  borderRadius: 4,
                  color: isActive ? "var(--accent-2)" : "var(--text-dim)",
                  background: isActive ? "var(--accent-soft)" : "transparent",
                  borderLeft: isActive && !collapsed
                    ? "2px solid var(--accent)"
                    : "2px solid transparent",
                  fontSize: 13,
                  transition: "color 120ms, background 120ms",
                  textDecoration: "none",
                })}
              >
                <n.icon size={collapsed ? 17 : 15} />
                {!collapsed && n.label}
              </NavLink>
            ))}

          {!token && !collapsed && (
            <div style={{ display: "grid", gap: 8 }}>
              <NavLink
                to="/login"
                data-testid="nav-login"
                className="btn-ghost"
                style={{ justifyContent: "center" }}
              >
                Sign in
              </NavLink>
              <NavLink
                to="/signup"
                data-testid="nav-signup"
                className="btn-primary"
                style={{ justifyContent: "center" }}
              >
                Sign up
              </NavLink>
            </div>
          )}

          {/* Recent Chats — collapsed: just a "+" new chat button */}
          {token && collapsed && (
            <div
              data-testid="recent-chats"
              style={{
                marginTop: 14,
                paddingTop: 12,
                borderTop: "1px solid var(--border)",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 4,
                flex: "1 1 auto",
              }}
            >
              <button
                data-testid="new-chat-btn"
                onClick={startNewSession}
                title="New chat"
                style={{
                  background: "none",
                  border: "1px solid var(--border-strong)",
                  color: "var(--accent-2)",
                  width: 34,
                  height: 34,
                  borderRadius: 4,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <Plus size={14} />
              </button>
            </div>
          )}

          {/* Recent Chats — expanded */}
          {token && !collapsed && (
            <div
              data-testid="recent-chats"
              style={{
                marginTop: 18,
                paddingTop: 14,
                borderTop: "1px solid var(--border)",
                display: "flex",
                flexDirection: "column",
                gap: 4,
                minHeight: 0,
                flex: "1 1 auto",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "0 8px 8px",
                }}
              >
                <span className="eyebrow" style={{ fontSize: 10 }}>
                  recent chats
                </span>
                <button
                  data-testid="new-chat-btn"
                  onClick={startNewSession}
                  title="New chat"
                  style={{
                    background: "none",
                    border: "1px solid var(--border-strong)",
                    color: "var(--accent-2)",
                    width: 22,
                    height: 22,
                    borderRadius: 4,
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <Plus size={12} />
                </button>
              </div>

              <div
                style={{
                  flex: 1,
                  overflowY: "auto",
                  display: "flex",
                  flexDirection: "column",
                  gap: 2,
                  paddingRight: 2,
                }}
              >
                {sessions.length === 0 && (
                  <p
                    data-testid="no-sessions"
                    style={{
                      fontSize: 11,
                      color: "var(--text-faint)",
                      padding: "8px 10px",
                    }}
                  >
                    No saved chats yet.
                  </p>
                )}
                {sessions.map((s) => {
                  const active = s.session_id === sessionId;
                  const label = (s.title && s.title.trim()) ||
                                 s.last_message ||
                                 "Untitled";
                  const display = label.length > 40 ? label.slice(0, 40) + "…" : label;
                  return (
                    <div
                      key={s.session_id}
                      data-testid={`session-row-${s.session_id}`}
                      onClick={() => openSession(s.session_id)}
                      role="button"
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        padding: "7px 10px",
                        borderRadius: 4,
                        background: active ? "var(--accent-soft)" : "transparent",
                        borderLeft: active
                          ? "2px solid var(--accent)"
                          : "2px solid transparent",
                        cursor: "pointer",
                        color: active ? "var(--accent-2)" : "var(--text-dim)",
                        fontSize: 12,
                        minWidth: 0,
                      }}
                      onMouseEnter={(e) =>
                        (e.currentTarget.style.background =
                          active ? "var(--accent-soft)" : "rgba(255,255,255,0.02)")
                      }
                      onMouseLeave={(e) =>
                        (e.currentTarget.style.background =
                          active ? "var(--accent-soft)" : "transparent")
                      }
                    >
                      <MessageSquare size={12} style={{ flexShrink: 0 }} />
                      <span
                        style={{
                          flex: 1,
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          fontWeight: s.title ? 500 : 400,
                        }}
                        title={label}
                      >
                        {display}
                      </span>
                      <button
                        data-testid={`delete-session-${s.session_id}`}
                        onClick={(e) => deleteSession(e, s.session_id)}
                        title="Delete chat"
                        style={{
                          background: "none",
                          border: "none",
                          color: "var(--text-faint)",
                          cursor: "pointer",
                          padding: 0,
                          opacity: 0.5,
                          transition: "opacity 120ms, color 120ms",
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.opacity = "1";
                          e.currentTarget.style.color = "var(--danger)";
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.opacity = "0.5";
                          e.currentTarget.style.color = "var(--text-faint)";
                        }}
                      >
                        <Trash2 size={11} />
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <div style={{ marginTop: "auto", display: "grid", gap: 10, paddingTop: 12 }}>
            {token && (
              <TokenBell tokens={tokensRemaining} unlimited={tokensUnlimited} collapsed={collapsed} />
            )}

            {/* Iter 212m-52 — theme toggle. Visible to every logged-in
                user in both expanded and collapsed sidebar states.
                Compact variant (icons only) shown when sidebar is
                collapsed so it doesn't overflow. */}
            {token && (
              <div
                style={{
                  display: "flex",
                  justifyContent: "center",
                  paddingBottom: 2,
                }}
              >
                <ThemeToggle compact={collapsed} />
              </div>
            )}

            {token && user && !collapsed && (
              <div
                data-testid="user-card"
                style={{
                  padding: 10,
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  fontSize: 12,
                }}
              >
                <div style={{ color: "var(--text)" }}>{user.name || user.email}</div>
                <div style={{ color: "var(--text-faint)", marginTop: 2 }}>
                  {tokensRemaining ?? user.tokens_remaining ?? "—"} tokens · {user.tier || "free"}
                </div>
                <button
                  data-testid="logout-btn"
                  onClick={logout}
                  style={{
                    marginTop: 10,
                    width: "100%",
                    background: "rgba(239,68,68,0.08)",
                    border: "1px solid rgba(239,68,68,0.35)",
                    color: "#ff8a8a",
                    cursor: "pointer",
                    fontSize: 11,
                    fontWeight: 600,
                    letterSpacing: "0.06em",
                    padding: "7px 10px",
                    borderRadius: 6,
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    gap: 6,
                    transition: "background 160ms ease, color 160ms ease, border-color 160ms ease",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "rgba(239,68,68,0.18)";
                    e.currentTarget.style.color = "#fff";
                    e.currentTarget.style.borderColor = "rgba(239,68,68,0.6)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "rgba(239,68,68,0.08)";
                    e.currentTarget.style.color = "#ff8a8a";
                    e.currentTarget.style.borderColor = "rgba(239,68,68,0.35)";
                  }}
                >
                  <LogOut size={12} /> Sign out
                </button>
              </div>
            )}

            {token && user && collapsed && (
              <div
                data-testid="user-card"
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: 8,
                }}
                title={`${user.name || user.email} · ${user.tokens_remaining ?? "—"} tokens`}
              >
                <div
                  style={{
                    width: 32,
                    height: 32,
                    borderRadius: 4,
                    background: "var(--accent-soft)",
                    border: "1px solid var(--border-strong)",
                    color: "var(--accent-2)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 12,
                    fontWeight: 600,
                    textTransform: "uppercase",
                  }}
                >
                  {(user.name || user.email || "?").slice(0, 2)}
                </div>
                <button
                  data-testid="logout-btn"
                  onClick={logout}
                  title="Sign out"
                  style={{
                    background: "rgba(239,68,68,0.10)",
                    border: "1px solid rgba(239,68,68,0.4)",
                    color: "#ff8a8a",
                    cursor: "pointer",
                    padding: 6,
                    borderRadius: 6,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    transition: "background 160ms ease, color 160ms ease, border-color 160ms ease",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "rgba(239,68,68,0.22)";
                    e.currentTarget.style.color = "#fff";
                    e.currentTarget.style.borderColor = "rgba(239,68,68,0.7)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "rgba(239,68,68,0.10)";
                    e.currentTarget.style.color = "#ff8a8a";
                    e.currentTarget.style.borderColor = "rgba(239,68,68,0.4)";
                  }}
                >
                  <LogOut size={13} />
                </button>
              </div>
            )}

            <div
              data-testid="health-pill"
              title={`api ${health?.ok ? "online" : "offline"}`}
              style={{
                fontSize: 10,
                fontFamily: "'JetBrains Mono', monospace",
                color: health?.ok ? "var(--ok)" : "var(--danger)",
                letterSpacing: "0.12em",
                display: "flex",
                alignItems: "center",
                justifyContent: collapsed ? "center" : "flex-start",
                gap: collapsed ? 0 : 0,
              }}
            >
              <span
                className="dot"
                style={{
                  background: health?.ok ? "var(--ok)" : "var(--danger)",
                  boxShadow: `0 0 12px ${health?.ok ? "var(--ok)" : "var(--danger)"}`,
                  marginRight: collapsed ? 0 : 8,
                }}
              />
              {!collapsed && `api ${health?.ok ? "online" : "offline"}`}
            </div>
          </div>
        </aside>

        <main
          data-testid="app-main"
          className={
            "aurem-main-padded" +
            (location.pathname === "/dashboard" ? " is-chat" : "")
          }
          style={{
            minWidth: 0,
            // padding owned by .aurem-main-padded so mobile can override
          }}
        >
          {children}
        </main>
      </div>
      )}
      {token && <PWAInstallPrompt />}
      {/* Iter 149 — Floating ORA Panel. Mounted only when an authenticated
          token is present so the panel inherits the logged-in user's JWT
          (never the admin token). */}
      {token && <FloatingORAButton />}
    </SessionCtx.Provider>
  );
}

export function PageHeader({ eyebrow, title, sub, right }) {
  return (
    <div
      data-testid="page-header"
      style={{
        display: "flex",
        alignItems: "flex-end",
        justifyContent: "space-between",
        gap: 24,
        marginBottom: 32,
        flexWrap: "wrap",
      }}
    >
      <div>
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h1
          className="serif"
          style={{ fontSize: 32, margin: "8px 0 6px", color: "var(--text)" }}
        >
          {title}
        </h1>
        {sub && (
          <p style={{ color: "var(--text-dim)", fontSize: 14, maxWidth: 580 }}>
            {sub}
          </p>
        )}
      </div>
      {right}
    </div>
  );
}
