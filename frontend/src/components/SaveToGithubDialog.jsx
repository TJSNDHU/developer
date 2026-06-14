/**
 * SaveToGithubDialog.jsx — Push generated files from the current session
 * to a GitHub repo via POST /api/aurem-dev/github/push.
 */
import React, { useEffect, useState } from "react";
import { X, Github, Send, AlertCircle, CheckCircle2 } from "lucide-react";
import { api } from "../lib/api";
import { toast } from "./Toast";

export default function SaveToGithubDialog({ open, onClose, sessionId }) {
  const [repo, setRepo] = useState("polarisbuiltinc-wq/auremdev");
  const [branch, setBranch] = useState("main");
  const [commit, setCommit] = useState("AUREM Dev: push from chat session");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null);
  const [ghConnected, setGhConnected] = useState(null);

  useEffect(() => {
    if (!open) return;
    setStatus(null);
    api.get("/github/status")
      .then((r) => setGhConnected(!!r.data?.connected))
      .catch(() => setGhConnected(false));
  }, [open]);

  if (!open) return null;

  async function push() {
    setBusy(true);
    setStatus(null);
    try {
      const r = await api.post("/github/push", {
        repo: repo.trim(),
        branch: branch.trim() || "main",
        commit_message: commit.trim() || "AUREM Dev push",
        session_id: sessionId,
      });
      setStatus(r.data);
      if (r.data?.ok) {
        toast({ message: `Pushed ${r.data.pushed}/${r.data.total} files to ${repo}@${branch}`, kind: "success" });
      } else {
        toast({ message: `Partial push: ${r.data.pushed}/${r.data.total}`, kind: "warn" });
      }
    } catch (e) {
      const msg = e?.response?.data?.detail || e.message || "Push failed";
      setStatus({ ok: false, error: msg });
      toast({ message: msg, kind: "error" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      data-testid="github-dialog-overlay"
      onClick={onClose}
      style={{
        // Iter 149 — instead of a full-screen blocking modal, keep the
        // chat visible and just slide a side panel in from the right.
        // The overlay is transparent + click-to-dismiss; only the
        // right column has visual presence.
        position: "fixed", inset: 0,
        background: "transparent",
        zIndex: 9000,
        pointerEvents: "auto",
      }}
    >
      <div
        data-testid="github-dialog"
        onClick={(e) => e.stopPropagation()}
        style={{
          position: "fixed",
          top: "50%",
          right: 24,
          transform: "translateY(-50%)",
          width: "min(440px, calc(100vw - 48px))",
          maxHeight: "85vh",
          overflowY: "auto",
          background: "var(--panel)",
          border: "1px solid var(--border-strong)",
          borderRadius: 8,
          padding: 24,
          color: "var(--text)",
          boxShadow: "0 24px 60px -12px rgba(0,0,0,0.7), -2px 0 32px rgba(0,0,0,0.4)",
          animation: "slide-in-right 0.18s ease-out",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 18 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Github size={18} style={{ color: "var(--accent-2)" }} />
            <h3 className="serif" style={{ margin: 0, fontSize: 20 }}>Save to GitHub</h3>
          </div>
          <button
            data-testid="github-dialog-close"
            onClick={onClose}
            style={{ background: "none", border: "none", color: "var(--text-faint)", cursor: "pointer" }}
          >
            <X size={18} />
          </button>
        </div>

        {ghConnected === false && (
          <div data-testid="github-not-connected" style={{
            marginBottom: 16, padding: 10,
            background: "rgba(255,197,96,0.08)",
            border: "1px solid rgba(255,197,96,0.25)",
            borderRadius: 4, color: "var(--accent-2)", fontSize: 12,
            display: "flex", alignItems: "flex-start", gap: 8,
          }}>
            <AlertCircle size={14} style={{ marginTop: 2, flexShrink: 0 }} />
            <span>
              <strong>GitHub not connected.</strong> Set <code>GITHUB_TOKEN</code> in <code>backend/.env</code> with a
              PAT (scope: <code>repo</code>), restart the backend, then retry.
            </span>
          </div>
        )}

        <p style={{ fontSize: 13, color: "var(--text-dim)", marginBottom: 18 }}>
          Push any <code>[File: path]</code> code blocks from this chat session into the chosen repo &amp; branch.
        </p>

        <div style={{ display: "grid", gap: 12 }}>
          <label>
            <span className="label-mini">Repository (owner/name)</span>
            <input data-testid="github-repo" className="input" value={repo}
                   onChange={(e) => setRepo(e.target.value)} />
          </label>
          <label>
            <span className="label-mini">Branch</span>
            <input data-testid="github-branch" className="input" value={branch}
                   onChange={(e) => setBranch(e.target.value)} />
          </label>
          <label>
            <span className="label-mini">Commit message</span>
            <input data-testid="github-commit" className="input" value={commit}
                   onChange={(e) => setCommit(e.target.value)} />
          </label>
        </div>

        {status && (
          <div data-testid="github-result" style={{
            marginTop: 16, padding: 12,
            background: "var(--bg-elev)",
            border: "1px solid var(--border)",
            borderRadius: 4, fontSize: 12,
            maxHeight: 200, overflowY: "auto",
          }}>
            {status.ok ? (
              <div style={{ color: "var(--ok)", display: "flex", alignItems: "center", gap: 6 }}>
                <CheckCircle2 size={13} /> Pushed {status.pushed}/{status.total} files
              </div>
            ) : (
              <div style={{ color: "var(--danger)" }}>{status.error || "Push failed"}</div>
            )}
            {Array.isArray(status.results) && status.results.length > 0 && (
              <ul style={{ listStyle: "none", padding: 0, margin: "8px 0 0", display: "grid", gap: 4 }}>
                {status.results.map((r, i) => (
                  <li key={i} style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
                                        color: r.ok ? "var(--ok)" : "var(--danger)" }}>
                    {r.ok ? "✓" : "✗"} {r.path} {r.commit ? `(${r.commit})` : r.error || ""}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <div style={{ display: "flex", gap: 10, marginTop: 20, justifyContent: "flex-end" }}>
          <button data-testid="github-cancel" className="btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            data-testid="github-push-btn"
            className="btn-primary"
            onClick={push}
            disabled={busy || !repo.trim() || ghConnected === false}
          >
            <Send size={13} /> {busy ? "Pushing…" : "Push to GitHub"}
          </button>
        </div>
      </div>
    </div>
  );
}
