/**
 * Shell.jsx — Application chrome (sidebar + top bar) shared across pages.
 */
import React, { useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, Rocket, Database, Globe, Settings as Cog,
  Coins, BarChart3, LogOut, Zap,
} from "lucide-react";
import { getUser, getToken, logout, healthApi } from "../lib/api";

const NAV = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard, testid: "nav-dashboard" },
  { to: "/deploy", label: "Deploy", icon: Rocket, testid: "nav-deploy" },
  { to: "/database", label: "Database", icon: Database, testid: "nav-database" },
  { to: "/domain", label: "Domain", icon: Globe, testid: "nav-domain" },
  { to: "/tokens", label: "Tokens", icon: Coins, testid: "nav-tokens" },
  { to: "/analytics", label: "Analytics", icon: BarChart3, testid: "nav-analytics" },
  { to: "/settings", label: "Settings", icon: Cog, testid: "nav-settings" },
];

export default function Shell({ children, requireAuth }) {
  const navigate = useNavigate();
  const user = getUser();
  const token = getToken();
  const [health, setHealth] = useState(null);

  useEffect(() => {
    if (requireAuth && !token) {
      navigate("/login", { replace: true });
    }
  }, [requireAuth, token, navigate]);

  useEffect(() => {
    healthApi.get("/health").then((r) => setHealth(r.data)).catch(() => setHealth({ ok: false }));
  }, []);

  return (
    <div style={{ minHeight: "100vh", display: "grid", gridTemplateColumns: "240px 1fr" }}>
      {/* Sidebar */}
      <aside
        data-testid="app-sidebar"
        style={{
          background: "var(--bg-elev)",
          borderRight: "1px solid var(--border)",
          padding: "28px 18px",
          display: "flex",
          flexDirection: "column",
          gap: 8,
          position: "sticky",
          top: 0,
          height: "100vh",
        }}
      >
        <div style={{ marginBottom: 28, paddingLeft: 6 }}>
          <NavLink to={token ? "/dashboard" : "/"} style={{ display: "flex", alignItems: "center", gap: 10 }} data-testid="brand-link">
            <Zap size={20} style={{ color: "var(--accent)" }} />
            <span className="serif" style={{ fontSize: 18, color: "var(--text)" }}>AUREM Dev</span>
          </NavLink>
          <span className="eyebrow" style={{ fontSize: 9, marginTop: 8, display: "block", paddingLeft: 30 }}>
            sovereign cto
          </span>
        </div>

        {token && NAV.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            data-testid={n.testid}
            style={({ isActive }) => ({
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "10px 12px",
              borderRadius: 4,
              color: isActive ? "var(--accent-2)" : "var(--text-dim)",
              background: isActive ? "var(--accent-soft)" : "transparent",
              borderLeft: isActive ? "2px solid var(--accent)" : "2px solid transparent",
              fontSize: 14,
              transition: "color 120ms, background 120ms",
              textDecoration: "none",
            })}
          >
            <n.icon size={16} />
            {n.label}
          </NavLink>
        ))}

        {!token && (
          <div style={{ display: "grid", gap: 8 }}>
            <NavLink to="/login" data-testid="nav-login" className="btn-ghost" style={{ justifyContent: "center" }}>Sign in</NavLink>
            <NavLink to="/signup" data-testid="nav-signup" className="btn-primary" style={{ justifyContent: "center" }}>Sign up</NavLink>
          </div>
        )}

        <div style={{ marginTop: "auto", display: "grid", gap: 10 }}>
          {token && user && (
            <div data-testid="user-card" style={{
              padding: 10, border: "1px solid var(--border)",
              borderRadius: 4, fontSize: 12,
            }}>
              <div style={{ color: "var(--text)" }}>{user.name || user.email}</div>
              <div style={{ color: "var(--text-faint)", marginTop: 2 }}>
                {user.tokens_remaining ?? "—"} tokens · {user.tier || "free"}
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
          <div data-testid="health-pill" style={{
            fontSize: 10, fontFamily: "'JetBrains Mono', monospace",
            color: health?.ok ? "var(--ok)" : "var(--danger)",
            letterSpacing: "0.12em",
          }}>
            <span className="dot" style={{ background: health?.ok ? "var(--ok)" : "var(--danger)", boxShadow: `0 0 12px ${health?.ok ? "var(--ok)" : "var(--danger)"}` }} />
            api {health?.ok ? "online" : "offline"}
          </div>
        </div>
      </aside>

      {/* Main */}
      <main data-testid="app-main" style={{ padding: "40px 56px", minWidth: 0 }}>
        {children}
      </main>
    </div>
  );
}

export function PageHeader({ eyebrow, title, sub, right }) {
  return (
    <div data-testid="page-header" style={{
      display: "flex", alignItems: "flex-end",
      justifyContent: "space-between", gap: 24,
      marginBottom: 32, flexWrap: "wrap",
    }}>
      <div>
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h1 className="serif" style={{ fontSize: 32, margin: "8px 0 6px", color: "var(--text)" }}>
          {title}
        </h1>
        {sub && <p style={{ color: "var(--text-dim)", fontSize: 14, maxWidth: 580 }}>{sub}</p>}
      </div>
      {right}
    </div>
  );
}
