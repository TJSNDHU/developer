/**
 * SystemSignalBanner.jsx — Core 3 of the verification foundation.
 *
 * Render typed, user-friendly banners for backend `system_signal`
 * records. We NEVER render the raw error string from the LLM.
 * Action buttons deep-link into the right product surface (PatModal,
 * EditDialog, retry).
 *
 * Backend contract — assistant messages may carry an array of signals:
 *   m.system_signals = [
 *     { signal: "github_auth_failed", severity: "error",
 *       tool: "read_repo_file", http_status: "401" },
 *     ...
 *   ]
 *
 * Iter 209 — Aurem CTO core architecture.
 */
import React from "react";
import { useNavigate } from "react-router-dom";
import { Key, Lock, AlertTriangle, Info, RefreshCw, ArrowRight } from "lucide-react";
import { getActiveProjectId } from "./TabBar";

const SIGNAL_CONFIG = {
  github_auth_failed: {
    color: "amber", Icon: Key,
    title: "GitHub access failed",
    body:  "PAT is missing, expired, or lacks repo scope.",
    action:{ label: "Update PAT", route: "/projects?pat={projectId}" },
  },
  github_permission_denied: {
    color: "amber", Icon: Lock,
    title: "Permission denied",
    body:  "Token exists but lacks write access. Regenerate with repo scope.",
    action:{ label: "Fix PAT", route: "/projects?pat={projectId}" },
  },
  repo_not_found: {
    color: "red", Icon: AlertTriangle,
    title: "Repo not found",
    body:  "Check the GitHub URL in project settings.",
    action:{ label: "Edit Project", route: "/projects?edit={projectId}" },
  },
  github_rate_limited: {
    color: "blue", Icon: RefreshCw,
    title: "GitHub rate limit hit",
    body:  "Too many calls in a short window. Wait ~60 seconds and retry.",
    action:{ label: "Retry", route: null },
  },
  github_server_error: {
    color: "blue", Icon: Info,
    title: "GitHub is having issues",
    body:  "Not your fault. Try again in a minute.",
    action:{ label: "Retry", route: null },
  },
  invalid_request: {
    color: "amber", Icon: AlertTriangle,
    title: "Request was invalid",
    body:  "The tool got malformed parameters. Try rephrasing.",
    action:null,
  },
  unknown_http_error: {
    color: "red", Icon: AlertTriangle,
    title: "Upstream error",
    body:  "Something unexpected came back from GitHub.",
    action:null,
  },
  unknown_error: {
    color: "red", Icon: AlertTriangle,
    title: "Tool failed",
    body:  "An unexpected error stopped the task.",
    action:null,
  },
};

const PALETTE = {
  amber: { bg: "rgba(245,158,11,0.08)", border: "rgba(245,158,11,0.35)", fg: "#f59e0b" },
  red:   { bg: "rgba(239,68,68,0.08)",  border: "rgba(239,68,68,0.35)",  fg: "#ef4444" },
  blue:  { bg: "rgba(59,130,246,0.08)", border: "rgba(59,130,246,0.35)", fg: "#3b82f6" },
};

export default function SystemSignalBanner({ signals }) {
  const navigate = useNavigate();
  if (!Array.isArray(signals) || signals.length === 0) return null;

  // Dedupe by signal name — multiple tools failing with the same root
  // cause should surface as a single banner, not a wall of red.
  const seen = new Set();
  const unique = signals.filter((s) => {
    if (!s?.signal || seen.has(s.signal)) return false;
    seen.add(s.signal);
    return true;
  });

  const projectId = getActiveProjectId() || "";

  return (
    <div data-testid="system-signal-banners" style={{ marginTop: 12, display: "grid", gap: 8 }}>
      {unique.map((sig) => {
        const cfg = SIGNAL_CONFIG[sig.signal] || SIGNAL_CONFIG.unknown_error;
        const pal = PALETTE[cfg.color] || PALETTE.red;
        const Icon = cfg.Icon || AlertTriangle;
        return (
          <div
            key={sig.signal}
            data-testid={`signal-${sig.signal}`}
            style={{
              padding: "10px 12px",
              background:  pal.bg,
              border: `1px solid ${pal.border}`,
              borderRadius: 8,
              display: "flex", alignItems: "flex-start", gap: 10,
            }}
          >
            <Icon size={16} style={{ color: pal.fg, flexShrink: 0, marginTop: 2 }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: pal.fg }}>
                {cfg.title}
                {sig.http_status ? (
                  <span style={{ marginLeft: 6, opacity: 0.7, fontWeight: 400 }}>
                    · HTTP {sig.http_status}
                  </span>
                ) : null}
              </div>
              <div style={{ fontSize: 12, color: "var(--text-dim, #94a3b8)", marginTop: 2, lineHeight: 1.5 }}>
                {cfg.body}
              </div>
            </div>
            {cfg.action ? (
              <button
                type="button"
                data-testid={`signal-${sig.signal}-action`}
                onClick={() => {
                  if (cfg.action.route) {
                    navigate(cfg.action.route.replace("{projectId}", projectId));
                  } else {
                    // No route → "Retry": just refresh the chat input focus.
                    window.dispatchEvent(new CustomEvent("ora:retry-last"));
                  }
                }}
                style={{
                  padding: "5px 10px",
                  background: pal.fg, color: "#0a0c10",
                  border: "none", borderRadius: 6,
                  fontSize: 11, fontWeight: 600, cursor: "pointer",
                  display: "inline-flex", alignItems: "center", gap: 4,
                  flexShrink: 0,
                  fontFamily: "'JetBrains Mono', monospace",
                }}
              >
                {cfg.action.label} <ArrowRight size={11} />
              </button>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
