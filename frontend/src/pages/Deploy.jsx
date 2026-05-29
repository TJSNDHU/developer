/**
 * Deploy.jsx — Manage deploy config & view recent runs.
 */
import React, { useEffect, useState } from "react";
import { Rocket, GitBranch, Server, History } from "lucide-react";
import Shell, { PageHeader } from "../components/Shell";
import { api } from "../lib/api";

export default function Deploy() {
  const [cfg, setCfg] = useState({
    repo_url: "", branch: "main", deploy_host: "", deploy_user: "root",
    deploy_repo_path: "/opt/app",
  });
  const [history, setHistory] = useState([]);
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const r = await api.get("/deploy/config");
        if (r.data?.config) setCfg({ ...cfg, ...r.data.config });
      } catch {}
      try {
        const r = await api.get("/deploy/history");
        setHistory(r.data?.runs || []);
      } catch {}
    })();
    // eslint-disable-next-line
  }, []);

  async function saveConfig(e) {
    e.preventDefault();
    setBusy(true);
    setStatus(null);
    try {
      await api.post("/deploy/config", cfg);
      setStatus({ ok: true, msg: "Deploy config saved." });
    } catch (err) {
      setStatus({ ok: false, msg: err?.response?.data?.detail || "Save failed" });
    } finally {
      setBusy(false);
    }
  }

  async function runDeploy() {
    setRunning(true);
    setStatus(null);
    try {
      const r = await api.post("/deploy/run", {});
      setStatus({ ok: true, msg: `Deploy queued (run ${r.data?.run_id ?? "—"})` });
      const r2 = await api.get("/deploy/history");
      setHistory(r2.data?.runs || []);
    } catch (err) {
      setStatus({ ok: false, msg: err?.response?.data?.detail || "Deploy failed" });
    } finally {
      setRunning(false);
    }
  }

  return (
    <Shell requireAuth>
      <PageHeader
        eyebrow="ship"
        title="Deploy"
        sub="Wire up your repo, target host, and let AUREM push it live."
      />

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 360px", gap: 24 }}>
        <form onSubmit={saveConfig} className="card" data-testid="deploy-config-form" style={{ display: "grid", gap: 14 }}>
          <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, display: "flex", alignItems: "center", gap: 8 }}>
            <GitBranch size={14} /> Deploy configuration
          </h3>
          <label>
            <span className="label-mini">Git repo URL</span>
            <input data-testid="deploy-repo-url" className="input" value={cfg.repo_url}
                   onChange={(e) => setCfg({ ...cfg, repo_url: e.target.value })}
                   placeholder="git@github.com:org/app.git" />
          </label>
          <label>
            <span className="label-mini">Branch</span>
            <input data-testid="deploy-branch" className="input" value={cfg.branch}
                   onChange={(e) => setCfg({ ...cfg, branch: e.target.value })} />
          </label>
          <label>
            <span className="label-mini">Host (IP or hostname)</span>
            <input data-testid="deploy-host" className="input" value={cfg.deploy_host}
                   onChange={(e) => setCfg({ ...cfg, deploy_host: e.target.value })}
                   placeholder="203.0.113.1" />
          </label>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 12 }}>
            <label>
              <span className="label-mini">SSH user</span>
              <input data-testid="deploy-user" className="input" value={cfg.deploy_user}
                     onChange={(e) => setCfg({ ...cfg, deploy_user: e.target.value })} />
            </label>
            <label>
              <span className="label-mini">Remote path</span>
              <input data-testid="deploy-path" className="input" value={cfg.deploy_repo_path}
                     onChange={(e) => setCfg({ ...cfg, deploy_repo_path: e.target.value })} />
            </label>
          </div>
          {status && (
            <div data-testid="deploy-status" style={{
              fontSize: 12, padding: "10px 12px", borderRadius: 4,
              color: status.ok ? "var(--ok)" : "var(--danger)",
              border: `1px solid ${status.ok ? "rgba(109,212,161,0.2)" : "rgba(255,107,107,0.2)"}`,
              background: status.ok ? "rgba(109,212,161,0.06)" : "rgba(255,107,107,0.06)",
            }}>
              {status.msg}
            </div>
          )}
          <div style={{ display: "flex", gap: 10 }}>
            <button type="submit" data-testid="deploy-save-btn" className="btn-ghost" disabled={busy}>
              {busy ? "Saving…" : "Save config"}
            </button>
            <button type="button" data-testid="deploy-run-btn" className="btn-primary"
                    disabled={running || !cfg.repo_url} onClick={runDeploy}>
              <Rocket size={14} /> {running ? "Deploying…" : "Deploy now"}
            </button>
          </div>
        </form>

        <div className="card" data-testid="deploy-history">
          <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 14, display: "flex", alignItems: "center", gap: 8 }}>
            <History size={14} /> Recent deploys
          </h3>
          {history.length === 0 ? (
            <p style={{ fontSize: 13, color: "var(--text-faint)" }}>No deploys yet.</p>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 10 }}>
              {history.slice(0, 8).map((h, i) => (
                <li key={i} data-testid={`deploy-history-row-${i}`} style={{
                  fontSize: 12, color: "var(--text-dim)",
                  display: "flex", alignItems: "center", gap: 8,
                  padding: "6px 0", borderBottom: "1px solid var(--border)",
                }}>
                  <Server size={11} style={{ color: h.ok ? "var(--ok)" : "var(--danger)" }} />
                  <span style={{ flex: 1 }}>{h.repo_url || h.target || "—"}</span>
                  <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10 }}>
                    {h.ts || h.created_at || ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Shell>
  );
}
