/**
 * Shell.jsx — Application chrome (sidebar + topbar) shared across pages.
 *
 * Sidebar contains brand, nav, "Recent Chats" section (when authenticated),
 * user card, and the api-online health pill.
 *
 * Chat session selection is exposed via Context — Dashboard subscribes,
 * other pages just consume the chrome.
 */
import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, Rocket, Database, Globe, Settings as Cog,
  Coins, BarChart3, LogOut, Zap, MessageSquare, Plus, Trash2,
  ChevronsLeft, ChevronsRight, FolderGit2,
} from "lucide-react";
import { api, getUser, getToken, logout, healthApi, newSessionId, setUser as saveUser } from "../lib/api";
import TokenBell from "./TokenBell";

const NAV = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard, testid: "nav-dashboard" },
  { to: "/projects", label: "Projects", icon: FolderGit2, testid: "nav-projects" },
  { to: "/deploy", label: "Deploy", icon: Rocket, testid: "nav-deploy" },
  { to: "/database", label: "Database", icon: Database, testid: "nav-database" },
  { to: "/domain", label: "Domain", icon: Globe, testid: "nav-domain" },
  { to: "/tokens", label: "Tokens", icon: Coins, testid: "nav-tokens" },
  { to: "/analytics", label: "Analytics", icon: BarChart3, testid: "nav-analytics" },
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
export default function Shell({ children, requireAuth }) {
  const navigate = useNavigate();
  const location = useLocation();
  const user = getUser();
  const token = getToken();
  const [health, setHealth] = useState({ ok: true, _initial: true });
  const [sessionId, setSessionIdState] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [collapsed, setCollapsedState] = useState(
    () => localStorage.getItem(COLLAPSED_KEY) === "1"
  );

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

  // When the active project changes (or on first mount), switch session
  useEffect(() => {
    const key = sessionKeyFor(activeProjectId);
    let existing = localStorage.getItem(key);
    if (!existing) {
      existing = newSessionId();
      localStorage.setItem(key, existing);
    }
    setSessionIdState(existing);
  }, [activeProjectId, sessionKeyFor]);

  const toggleCollapsed = useCallback(() => {
    setCollapsedState((c) => {
      const next = !c;
      localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0");
      return next;
    });
  }, []);

  // ── Token wallet polling ───────────────────────────────────────────
  const [tokensRemaining, setTokensRemaining] = useState(
    () => (getUser() || {}).tokens_remaining ?? null
  );

  const refreshTokens = useCallback(async () => {
    if (!token) return;
    try {
      const r = await api.get("/auth/tokens");
      const t = r.data?.tokens_remaining;
      if (typeof t === "number") {
        setTokensRemaining(t);
        const u = getUser();
        if (u) saveUser({ ...u, tokens_remaining: t });
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

  return (
    <SessionCtx.Provider value={{
      sessionId, setSessionId, refreshSessions,
      tokensRemaining, setTokensRemaining, refreshTokens,
    }}>
      <div
        style={{
          minHeight: "100vh",
          display: "grid",
          gridTemplateColumns: `${collapsed ? 64 : 260}px 1fr`,
          transition: "grid-template-columns 240ms cubic-bezier(0.4, 0, 0.2, 1)",
        }}
      >
        <aside
          data-testid="app-sidebar"
          data-collapsed={collapsed ? "true" : "false"}
          style={{
            background: "var(--bg-elev)",
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
            <NavLink
              to={token ? "/dashboard" : "/"}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                paddingLeft: collapsed ? 0 : 6,
              }}
              data-testid="brand-link"
              title="AUREM Dev"
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
            </NavLink>
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
              <TokenBell tokens={tokensRemaining} collapsed={collapsed} />
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
                    background: "none",
                    border: "none",
                    color: "var(--text-faint)",
                    cursor: "pointer",
                    fontSize: 11,
                    padding: "8px 0 0",
                    display: "flex",
                    alignItems: "center",
                    gap: 4,
                  }}
                >
                  <LogOut size={11} /> Sign out
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
                    background: "none",
                    border: "none",
                    color: "var(--text-faint)",
                    cursor: "pointer",
                    padding: 4,
                    display: "flex",
                  }}
                  onMouseEnter={(e) =>
                    (e.currentTarget.style.color = "var(--danger)")
                  }
                  onMouseLeave={(e) =>
                    (e.currentTarget.style.color = "var(--text-faint)")
                  }
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
          style={{
            padding: location.pathname === "/dashboard" ? 0 : "40px 56px",
            minWidth: 0,
          }}
        >
          {children}
        </main>
      </div>
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
