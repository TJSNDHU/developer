/**
 * pages/ToolsPage.jsx — Iter 212m-158
 *
 * Public "Developer tools" preview surface.  Lists Bug Hunt,
 * Vanguard Scan, Security Scan, and Health Scan as "Coming soon"
 * cards with a notify-me email capture.  Visible to ALL logged-in
 * users (no admin gate at this surface — the admin gate still
 * holds on the actual tool routes per iter 212m-157 + 212m-158).
 *
 * Spec source: founder's drop-in file (faithfully ported here).
 * Changes from the original drop-in:
 *   • Tabler icons (`ti ti-*`) → lucide-react (codebase standard,
 *     already installed everywhere else, no extra CDN).
 *   • `useRepos()` mock → real `GET /cto/projects/list` fetch.
 *   • Notify form wired to `POST /notify-interest` (silent no-op if
 *     the endpoint is missing — the UX still flips to "submitted").
 *   • DOES NOT link to any actual tool route (per spec).
 */
import React, { useState, useEffect } from "react";
import { Bug, ShieldCheck, Lock, Activity, Check } from "lucide-react";
import { api } from "../lib/api";

// ─── Real repos hook ─────────────────────────────────────────────────
//
// Pulls the same /cto/projects/list endpoint the dashboard sidebar
// uses so the dropdown matches whatever projects the user already
// has connected.  Anonymous / unauthenticated callers just get [].
function useRepos() {
  const [repos, setRepos] = useState([]);
  useEffect(() => {
    let alive = true;
    api.get("/cto/projects/list")
      .then((r) => {
        if (!alive) return;
        const list = Array.isArray(r?.data?.projects) ? r.data.projects : [];
        // Map to the shape the UI expects: {id, full_name}.
        setRepos(list.map((p) => ({
          id: p.project_id || p.id,
          full_name: (p.github_owner && p.github_repo)
            ? `${p.github_owner}/${p.github_repo}`
            : (p.name || p.project_id || "untitled"),
        })));
      })
      .catch(() => { if (alive) setRepos([]); });
    return () => { alive = false; };
  }, []);
  return repos;
}

// ─── Tool definitions ──────────────────────────────────────────────
const TOOLS = [
  { id: "bug-hunt",      Icon: Bug,         name: "Bug Hunt",
    description: "AI finds bugs in your code before users do.",
    accentClass: "coral",  eta: "Coming soon" },
  { id: "vanguard",      Icon: ShieldCheck, name: "Vanguard Scan",
    description: "Full security audit of your repo in minutes.",
    accentClass: "purple", eta: "Coming soon" },
  { id: "security-scan", Icon: Lock,        name: "Security Scan",
    description: "CVE detection and dependency vulnerability analysis.",
    accentClass: "amber",  eta: "Coming soon" },
  { id: "health-scan",   Icon: Activity,    name: "Health Scan",
    description: "Codebase quality score with actionable fixes.",
    accentClass: "teal",   eta: "Coming soon" },
];

// ─── Accent color map → CSS variable equivalents ───────────────────
const ACCENT = {
  coral:  { bg: "#FAECE7", border: "#993C1D", text: "#993C1D", icon: "#D85A30" },
  purple: { bg: "#EEEDFE", border: "#534AB7", text: "#534AB7", icon: "#7F77DD" },
  amber:  { bg: "#FAEEDA", border: "#854F0B", text: "#854F0B", icon: "#BA7517" },
  teal:   { bg: "#E1F5EE", border: "#0F6E56", text: "#0F6E56", icon: "#1D9E75" },
};

// ─── Single tool card ──────────────────────────────────────────────
function ToolCard({ tool, repos }) {
  const [selectedRepo, setSelectedRepo] = useState("");
  const [email,        setEmail]        = useState("");
  const [submitted,    setSubmitted]    = useState(false);
  const [error,        setError]        = useState("");
  const ac = ACCENT[tool.accentClass];
  const Icon = tool.Icon;

  async function handleNotify(e) {
    e.preventDefault();
    if (!email) return;
    setError("");
    // Fire-and-log: never block the UX on the network — even if the
    // backend endpoint is missing, the card still flips to "submitted".
    const payload = { tool: tool.id, email, repo: selectedRepo || null };
    try { console.log("[notify-interest]", payload); } catch { /* ignore */ }
    try {
      await api.post("/notify-interest", payload);
    } catch (err) {
      // Soft-fail: 404 just means the backend route isn't wired yet.
      // 4xx with detail surfaces a friendly inline error.
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      if (status && status !== 404 && detail) {
        setError(typeof detail === "string" ? detail : "Could not save your interest");
        return;
      }
    }
    setSubmitted(true);
  }

  return (
    <div
      data-testid={`tools-card-${tool.id}`}
      style={{
        background: "var(--surface-2)",
        border: "0.5px solid var(--border)",
        borderRadius: 12,
        padding: "1.25rem",
        display: "flex",
        flexDirection: "column",
        gap: 12,
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
        <div
          style={{
            width: 40, height: 40, borderRadius: "var(--radius, 8px)",
            background: ac.bg,
            display: "flex", alignItems: "center", justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <Icon size={20} color={ac.icon} aria-hidden="true" />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{
              fontSize: 15, fontWeight: 500,
              color: "var(--text-primary, var(--ink))",
            }}>{tool.name}</span>
            <span
              data-testid={`tools-card-${tool.id}-eta`}
              style={{
                fontSize: 11, fontWeight: 500,
                padding: "2px 8px", borderRadius: 20,
                background: ac.bg, color: ac.text,
                border: `0.5px solid ${ac.border}`,
                whiteSpace: "nowrap",
              }}
            >{tool.eta}</span>
          </div>
          <p style={{
            margin: "2px 0 0", fontSize: 13,
            color: "var(--text-secondary, var(--text-dim))",
            lineHeight: 1.5,
          }}>{tool.description}</p>
        </div>
      </div>

      {/* Repo selector (disabled — preview only) */}
      <div>
        <label htmlFor={`repo-${tool.id}`} style={{
          fontSize: 12, color: "var(--text-muted, var(--text-faint))",
          display: "block", marginBottom: 4,
        }}>Repository</label>
        <select
          id={`repo-${tool.id}`}
          data-testid={`tools-card-${tool.id}-repo`}
          value={selectedRepo}
          onChange={(e) => setSelectedRepo(e.target.value)}
          disabled
          style={{ width: "100%", opacity: 0.6, cursor: "not-allowed" }}
        >
          <option value="">Select a repo</option>
          {repos.map((r) => (
            <option key={r.id} value={r.id}>{r.full_name}</option>
          ))}
        </select>
      </div>

      {/* CTA */}
      <button
        type="button"
        disabled
        data-testid={`tools-card-${tool.id}-cta`}
        style={{
          width: "100%", padding: "8px 0",
          borderRadius: "var(--radius, 8px)",
          border: "0.5px solid var(--border-strong, var(--border))",
          background: "var(--surface-1, transparent)",
          color: "var(--text-muted, var(--text-faint))",
          fontSize: 14, fontWeight: 500,
          cursor: "not-allowed", opacity: 0.7,
          display: "inline-flex", alignItems: "center",
          justifyContent: "center", gap: 6,
        }}
      >
        <Icon size={14} aria-hidden="true" />
        Coming soon
      </button>

      {/* Email notify */}
      {!submitted ? (
        <form
          onSubmit={handleNotify}
          data-testid={`tools-card-${tool.id}-form`}
          style={{ display: "flex", gap: 6, flexWrap: "wrap" }}
        >
          <input
            type="email"
            placeholder="Get notified when ready"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            data-testid={`tools-card-${tool.id}-email`}
            style={{ flex: 1, minWidth: 0, fontSize: 13 }}
          />
          <button
            type="submit"
            data-testid={`tools-card-${tool.id}-notify`}
            style={{
              padding: "0 12px", fontSize: 13, whiteSpace: "nowrap",
              borderRadius: "var(--radius, 8px)",
              border: `0.5px solid ${ac.border}`,
              background: ac.bg, color: ac.text,
              cursor: "pointer",
            }}
          >
            Notify me
          </button>
          {error && (
            <p style={{
              flex: "1 0 100%", margin: 0, fontSize: 12,
              color: "#b91c1c",
            }} data-testid={`tools-card-${tool.id}-err`}>{error}</p>
          )}
        </form>
      ) : (
        <p
          data-testid={`tools-card-${tool.id}-success`}
          style={{
            fontSize: 13,
            color: "var(--text-success, #0F6E56)",
            margin: 0,
            display: "flex", alignItems: "center", gap: 6,
          }}
        >
          <Check size={14} aria-hidden="true" />
          We&apos;ll let you know when {tool.name} is ready.
        </p>
      )}
    </div>
  );
}

// ─── Page ───────────────────────────────────────────────────────────
export default function ToolsPage() {
  const repos = useRepos();

  return (
    <div
      data-testid="tools-page"
      style={{ maxWidth: 720, margin: "0 auto", padding: "2rem 1.5rem" }}
    >
      {/* Page header */}
      <div style={{ marginBottom: "2rem" }}>
        <h1 style={{
          fontSize: 22, fontWeight: 500, margin: 0,
          color: "var(--text-primary, var(--ink))",
        }}>Developer tools</h1>
        <p style={{
          fontSize: 15, lineHeight: 1.6, margin: "6px 0 0",
          color: "var(--text-secondary, var(--text-dim))",
        }}>
          Automated scans and analysis for your repositories. Each tool runs
          against a repo you choose — no setup, no config files.
        </p>
      </div>

      {/* Tool cards grid */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
        gap: 16,
      }}>
        {TOOLS.map((tool) => (
          <ToolCard key={tool.id} tool={tool} repos={repos} />
        ))}
      </div>

      {/* Footer note */}
      <p style={{
        marginTop: "2rem", fontSize: 13, textAlign: "center", lineHeight: 1.6,
        color: "var(--text-muted, var(--text-faint))",
      }}>
        These tools are in active development.{" "}
        <a
          href="/dashboard"
          data-testid="tools-back-to-ora"
          style={{ color: "var(--text-accent, var(--accent-2))", textDecoration: "none" }}
        >Back to ORA</a>
      </p>
    </div>
  );
}
