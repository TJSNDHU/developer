/**
 * GitHubCard.jsx — Connect / Disconnect GitHub OAuth + show repos.
 */
import React, { useEffect, useState, useCallback } from "react";
import { Github, ExternalLink, Plug, Unlink, Lock, FolderGit2 } from "lucide-react";
import { api, API_BASE, getToken } from "../lib/api";
import { toast } from "./Toast";

export default function GitHubCard() {
  const [status, setStatus] = useState(null);
  const [repos, setRepos] = useState([]);
  const [loadingRepos, setLoadingRepos] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const r = await api.get("/github/oauth/status");
      setStatus(r.data);
    } catch {
      setStatus({ connected: false });
    }
  }, []);

  useEffect(() => {
    refresh();
    // If we arrived from the OAuth callback redirect, surface a toast + clean URL
    const p = new URLSearchParams(window.location.search);
    if (p.get("github") === "connected") {
      toast({ message: `GitHub connected as @${p.get("login")}`, kind: "success" });
      window.history.replaceState({}, "", "/settings");
    } else if (p.get("github") === "error") {
      toast({ message: `GitHub connect failed: ${p.get("msg") || "unknown"}`, kind: "error" });
      window.history.replaceState({}, "", "/settings");
    }
  }, [refresh]);

  function connect() {
    const tok = getToken();
    // GitHub doesn't forward arbitrary headers, so we open the connect endpoint
    // in the current window. JWT travels as an `?auth=` query param that the
    // backend reads as a fallback (see github_oauth router).
    window.location.href = `${API_BASE}/github/oauth/connect?auth=${encodeURIComponent(tok || "")}`;
  }

  async function disconnect() {
    try {
      await api.delete("/github/oauth/disconnect");
      setStatus({ connected: false });
      setRepos([]);
      toast({ message: "GitHub disconnected.", kind: "info" });
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Disconnect failed", kind: "error" });
    }
  }

  async function loadRepos() {
    setLoadingRepos(true);
    try {
      const r = await api.get("/github/oauth/repos");
      setRepos(r.data?.repos || []);
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Couldn't fetch repos", kind: "error" });
    } finally {
      setLoadingRepos(false);
    }
  }

  if (!status) return null;

  return (
    <section data-testid="settings-github-oauth" className="card" style={{ gridColumn: "1 / -1" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <Github size={20} style={{ color: "var(--accent-2)", flexShrink: 0 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="serif" style={{ fontSize: 16 }}>GitHub</div>
          <div style={{ fontSize: 12, color: "var(--text-faint)" }}>
            {status.connected
              ? `Connected as @${status.login}`
              : "Connect to push generated projects to your own GitHub."}
          </div>
        </div>
        {status.connected ? (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {status.avatar_url && (
              <img
                src={status.avatar_url}
                alt={status.login}
                width={26}
                height={26}
                style={{ borderRadius: "50%", border: "1px solid var(--border-strong)" }}
              />
            )}
            <button
              data-testid="github-load-repos"
              className="btn-ghost"
              onClick={loadRepos}
              disabled={loadingRepos}
              style={{ padding: "6px 10px", fontSize: 11 }}
            >
              <FolderGit2 size={11} /> {loadingRepos ? "Loading…" : "My Repos"}
            </button>
            <button
              data-testid="github-disconnect"
              onClick={disconnect}
              className="btn-ghost"
              style={{
                padding: "6px 10px", fontSize: 11,
                borderColor: "rgba(255,107,107,0.3)", color: "var(--danger)",
              }}
            >
              <Unlink size={11} /> Disconnect
            </button>
          </div>
        ) : (
          <button
            data-testid="github-connect"
            onClick={connect}
            className="btn-primary"
            style={{ padding: "8px 14px", fontSize: 12 }}
          >
            <Plug size={13} /> Connect GitHub
          </button>
        )}
      </div>

      {repos.length > 0 && (
        <div data-testid="github-repo-list" style={{
          marginTop: 14, paddingTop: 14,
          borderTop: "1px solid var(--border)",
        }}>
          <span className="label-mini">Your repositories</span>
          <ul style={{ listStyle: "none", padding: 0, margin: "8px 0 0", display: "grid", gap: 4 }}>
            {repos.slice(0, 10).map((r) => (
              <li key={r.full_name} style={{
                display: "flex", alignItems: "center", gap: 6,
                fontSize: 12, color: "var(--text-dim)",
                padding: "6px 0", borderBottom: "1px solid var(--border)",
              }}>
                {r.private ? <Lock size={11} /> : <FolderGit2 size={11} />}
                <span style={{ flex: 1, fontFamily: "'JetBrains Mono', monospace" }}>
                  {r.full_name}
                </span>
                <a href={r.url} target="_blank" rel="noreferrer"
                   style={{ color: "var(--text-faint)" }}>
                  <ExternalLink size={11} />
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
