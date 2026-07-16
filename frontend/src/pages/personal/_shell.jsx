/**
 * pages/personal/_shell.jsx — Shared shell components for Personal Track.
 *
 * Design rule: logo/top-nav/account area MUST be consistent with the
 * Developer Track — only the CONTENT area's theme (cream/terracotta)
 * changes. This shell is the light-mode wrapper around every
 * Personal-Track page.
 */
import React from "react";
import { Link, useNavigate } from "react-router-dom";
import { LogOut, Settings as SettingsIcon } from "lucide-react";
import { getUser, logout as clearSession } from "../../lib/api";

export function PersonalShell({ children, headerRight = null }) {
  const nav = useNavigate();
  const s = getUser();
  return (
    <div
      data-testid="personal-shell"
      style={{
        minHeight: "100vh",
        background: "#FDFDF9",
        fontFamily: "'Manrope', system-ui, -apple-system, sans-serif",
        color: "#1C1C19",
      }}
    >
      {/* Grain overlay — 2% opacity SVG noise for depth. */}
      <div
        aria-hidden="true"
        style={{
          position: "fixed", inset: 0, pointerEvents: "none",
          opacity: 0.02, zIndex: 0,
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")",
        }}
      />

      {/* Shared header — same wordmark + account menu as Developer Track,
          but in a light surface so it complements the Personal Track body.  */}
      <header
        data-testid="personal-header"
        style={{
          position: "relative", zIndex: 10,
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "20px 28px",
          borderBottom: "1px solid #E5E5DF",
          background: "rgba(253,253,249,0.85)",
          backdropFilter: "blur(12px)",
        }}
      >
        <Link
          to={s ? "/build" : "/"}
          data-testid="personal-logo"
          style={{
            textDecoration: "none",
            fontFamily: "'Cabinet Grotesk', 'Manrope', sans-serif",
            fontWeight: 800, fontSize: 20, letterSpacing: "-0.02em",
            color: "#1C1C19",
          }}
        >
          AUREM
        </Link>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {headerRight}
          {s && (
            <>
              <Link
                to="/settings"
                data-testid="personal-settings-link"
                title="Settings"
                style={{
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  width: 36, height: 36, borderRadius: 10,
                  color: "#6B6B63", textDecoration: "none",
                  transition: "background 200ms ease",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(28,28,25,0.05)")}
                onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
              >
                <SettingsIcon size={18} />
              </Link>
              <button
                data-testid="personal-logout"
                onClick={() => { clearSession(); nav("/login", { replace: true }); }}
                title="Sign out"
                style={{
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  width: 36, height: 36, borderRadius: 10,
                  border: "none", background: "transparent",
                  color: "#6B6B63", cursor: "pointer",
                  transition: "background 200ms ease",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(28,28,25,0.05)")}
                onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
              >
                <LogOut size={18} />
              </button>
            </>
          )}
        </div>
      </header>

      <main style={{ position: "relative", zIndex: 1 }}>{children}</main>
    </div>
  );
}

/** Primary CTA in Personal Track's terracotta. */
export function PrimaryButton({ children, disabled, "data-testid": tid, ...rest }) {
  return (
    <button
      data-testid={tid}
      disabled={disabled}
      {...rest}
      style={{
        display: "inline-flex", alignItems: "center", gap: 8,
        padding: "12px 22px",
        borderRadius: 999,
        background: disabled ? "#E5E5DF" : "#E07A5F",
        color: disabled ? "#8B8B7D" : "#FFFFFF",
        border: "none", cursor: disabled ? "not-allowed" : "pointer",
        fontFamily: "inherit", fontSize: 14, fontWeight: 600,
        letterSpacing: "-0.01em",
        boxShadow: disabled ? "none" : "0 4px 14px rgba(224,122,95,0.28)",
        transition: "transform 200ms ease, box-shadow 200ms ease, background 200ms ease",
        ...rest.style,
      }}
      onMouseEnter={(e) => {
        if (disabled) return;
        e.currentTarget.style.transform = "translateY(-2px)";
        e.currentTarget.style.background = "#D56A4F";
      }}
      onMouseLeave={(e) => {
        if (disabled) return;
        e.currentTarget.style.transform = "translateY(0)";
        e.currentTarget.style.background = "#E07A5F";
      }}
    >
      {children}
    </button>
  );
}

/** Secondary button — muted, for regenerate/back actions. */
export function SecondaryButton({ children, "data-testid": tid, ...rest }) {
  return (
    <button
      data-testid={tid}
      {...rest}
      style={{
        display: "inline-flex", alignItems: "center", gap: 8,
        padding: "12px 20px",
        borderRadius: 999,
        background: "transparent",
        color: "#1C1C19",
        border: "1px solid #E5E5DF",
        cursor: "pointer",
        fontFamily: "inherit", fontSize: 14, fontWeight: 500,
        transition: "background 200ms ease, border-color 200ms ease",
        ...rest.style,
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = "rgba(28,28,25,0.04)";
        e.currentTarget.style.borderColor = "#D5D5CF";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "transparent";
        e.currentTarget.style.borderColor = "#E5E5DF";
      }}
    >
      {children}
    </button>
  );
}
