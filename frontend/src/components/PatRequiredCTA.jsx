/**
 * PatRequiredCTA.jsx — Inline "Add PAT" button inside chat bubbles.
 *
 * When ORA (or any assistant) hits a GitHub 401 and tells the user to
 * fix their PAT, we surface a one-click CTA right inside the bubble
 * that deep-links to /projects?pat=<active-project-id>. Projects.jsx
 * picks up that query param and opens the PatModal for the right
 * project so the user doesn't have to hunt through Project Settings.
 *
 * Detection is intentionally conservative — we only fire when the
 * message contains multiple PAT signals so we never show the CTA on
 * incidental mentions of "GitHub" or "token".
 */
import React from "react";
import { useNavigate } from "react-router-dom";
import { Key, ArrowRight } from "lucide-react";
import { getActiveProjectId } from "./TabBar";

const PAT_SIGNALS = [
  /\b401\b.*\b(github|bad credentials|authentication|unauthor)/i,
  /\b(bad credentials|invalid credentials)\b/i,
  /\bpersonal access token\b/i,
  /\bgithub pat\b/i,
  /\bfine[- ]grained pat\b/i,
  /\b(update|fix|regenerate|generate).{0,40}\bpat\b/i,
  /\bcontents:\s*read/i,
];

function needsPat(text) {
  if (!text || typeof text !== "string") return false;
  let hits = 0;
  for (const re of PAT_SIGNALS) if (re.test(text)) hits++;
  // Require ≥2 distinct signals to avoid false positives on casual mentions.
  return hits >= 2;
}

export default function PatRequiredCTA({ text }) {
  const navigate = useNavigate();
  if (!needsPat(text)) return null;

  const projectId = getActiveProjectId();
  const target = projectId
    ? `/projects?pat=${encodeURIComponent(projectId)}`
    : `/projects?add=1`;

  return (
    <div data-testid="chat-pat-cta" style={{
      marginTop: 12, padding: "10px 12px",
      background: "rgba(245,158,11,0.08)",
      border: "1px solid rgba(245,158,11,0.3)",
      borderRadius: 8,
      display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
    }}>
      <Key size={14} style={{ color: "#f59e0b", flexShrink: 0 }} />
      <span style={{ fontSize: 12, color: "var(--text-dim, #94a3b8)", flex: 1, minWidth: 0 }}>
        Need a GitHub Personal Access Token to scan this repo. ORA can guide you in 30 seconds.
      </span>
      <button
        type="button"
        data-testid="chat-pat-cta-btn"
        onClick={() => navigate(target)}
        style={{
          padding: "6px 12px",
          background: "#f59e0b", color: "#0a0c10",
          border: "none", borderRadius: 6,
          fontSize: 12, fontWeight: 600, cursor: "pointer",
          display: "inline-flex", alignItems: "center", gap: 5,
          fontFamily: "'JetBrains Mono', monospace", letterSpacing: "0.04em",
          flexShrink: 0,
        }}
      >
        Add PAT <ArrowRight size={11} />
      </button>
    </div>
  );
}
