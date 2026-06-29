/**
 * components/chat/RepoHelpDialog.jsx — Iter 212m-153
 *
 * (Originally Iter 148.)
 *
 * Lightweight modal that explains exactly how to wire a GitHub repo
 * to the currently active project.  We surface it instead of pushing
 * the user out to `/projects` blind so they understand the *why*
 * before being asked to enter owner/repo.  Keeps shipping unblocked.
 *
 * Props:
 *   project          The currently active project (or null).
 *   onClose          Backdrop / "Later" handler.
 *   onOpenProjects   "Open Projects →" navigation handler.
 */
import React from "react";

export default function RepoHelpDialog({ project, onClose, onOpenProjects }) {
  return (
    <div
      data-testid="repo-help-overlay"
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 1000,
        background: "rgba(8, 11, 18, 0.62)",
        backdropFilter: "blur(8px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        data-testid="repo-help-dialog"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(520px, 100%)",
          background: "linear-gradient(180deg, rgba(20,24,34,0.95), rgba(13,16,24,0.95))",
          border: "1px solid rgba(255,138,42,0.28)",
          borderRadius: 14,
          padding: "24px 26px",
          boxShadow: "0 20px 60px rgba(0,0,0,0.55), 0 0 0 1px rgba(255,255,255,0.02) inset",
          color: "var(--text)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
          <span
            style={{
              width: 32, height: 32, borderRadius: 8,
              background: "rgba(239,68,68,0.16)",
              border: "1px solid rgba(239,68,68,0.5)",
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              fontSize: 16,
            }}
          >⚠</span>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 14, fontWeight: 600, letterSpacing: "0.02em" }}>
              No GitHub repo connected
            </div>
            <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 2 }}>
              {project?.name
                ? `Project "${project.name}" is not linked to any repository yet.`
                : "Select a project first, then link a repository."}
            </div>
          </div>
        </div>

        <div style={{
          fontSize: 12, color: "var(--text-dim)", lineHeight: 1.55,
          marginBottom: 14,
        }}>
          Once linked, AUREM can commit code changes directly to your
          GitHub repository on every successful task. Without a repo,
          shipped work stays inside the chat session only.
        </div>

        <div style={{
          background: "rgba(255,255,255,0.02)",
          border: "1px solid var(--border)",
          borderRadius: 10,
          padding: "14px 16px",
          marginBottom: 16,
        }}>
          <div style={{
            fontSize: 10, fontFamily: "'JetBrains Mono', monospace",
            letterSpacing: "0.18em", color: "var(--accent-2)",
            marginBottom: 10,
          }}>HOW TO CONNECT — 3 STEPS</div>
          <ol style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.7 }}>
            <li>Open the <strong>Projects</strong> page from the sidebar (or click the button below).</li>
            <li>Click <strong>Edit</strong> on the project card you want to connect.</li>
            <li>Fill in <code style={{
              background: "rgba(255,255,255,0.06)", padding: "1px 6px",
              borderRadius: 4, fontSize: 11,
            }}>github_owner</code> and <code style={{
              background: "rgba(255,255,255,0.06)", padding: "1px 6px",
              borderRadius: 4, fontSize: 11,
            }}>github_repo</code> with your repository details, then save.</li>
          </ol>
        </div>

        <div style={{
          fontSize: 11, color: "var(--text-faint)", marginBottom: 16,
          padding: "8px 12px", background: "rgba(255,197,96,0.06)",
          border: "1px solid rgba(255,197,96,0.2)", borderRadius: 8,
        }}>
          💡 Tip: the repo must already exist on GitHub and your AUREM
          installation must have push access. New repo?{" "}
          <a
            href="https://github.com/new"
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: "var(--accent-2)", textDecoration: "underline" }}
          >Create one here</a>.
        </div>

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button
            type="button"
            data-testid="repo-help-cancel"
            onClick={onClose}
            className="btn-ghost"
            style={{ fontSize: 12 }}
          >
            Later
          </button>
          <button
            type="button"
            data-testid="repo-help-open-projects"
            onClick={onOpenProjects}
            className="btn-primary"
            style={{ fontSize: 12, gap: 6 }}
          >
            Open Projects →
          </button>
        </div>
      </div>
    </div>
  );
}
