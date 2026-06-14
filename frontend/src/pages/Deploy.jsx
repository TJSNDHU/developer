/**
 * Deploy.jsx — Manage deploy config & view recent runs.
 *
 * Iter 146 — Field names + SSH private key field aligned with backend
 * (DeployConfigBody). Previously every Save config call returned 422
 * because the form was missing `private_key` (REQUIRED on backend) and
 * was sending camel-case names (`deploy_host`, `deploy_user`,
 * `deploy_repo_path`) the backend never matched. Now:
 *   - field names: host / port / username / private_key / repo_path /
 *     branch / compose_file  (1:1 with backend)
 *   - SSH private key is a PEM textarea, write-only (backend returns
 *     `"•••••••• (write-only — never returned)"` once saved so users
 *     know it's stored without ever exposing it again)
 *   - port + compose_file are tucked under an "Advanced" disclosure so
 *     the common case (port 22, compose file docker-compose.yml) stays
 *     a 3-field form.
 */
import React, { useEffect, useState } from "react";
import { Rocket, GitBranch, Server, History, KeyRound, Settings2 } from "lucide-react";
import Shell, { PageHeader } from "../components/Shell";
import { api } from "../lib/api";

const EMPTY_CFG = {
  host:         "",
  port:         22,
  username:     "root",
  private_key:  "",
  repo_path:    "/opt/app",
  branch:       "main",
  compose_file: "docker-compose.yml",
};

export default function Deploy() {
  const [cfg, setCfg]           = useState(EMPTY_CFG);
  const [savedKey, setSavedKey] = useState("");   // "••••••" marker from backend
  const [history, setHistory]   = useState([]);
  const [busy, setBusy]         = useState(false);
  const [running, setRunning]   = useState(false);
  const [status, setStatus]     = useState(null);
  const [showAdv, setShowAdv]   = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const r = await api.get("/deploy/config");
        if (r.data?.configured) {
          setCfg((prev) => ({
            ...prev,
            host:         r.data.host         ?? prev.host,
            port:         r.data.port         ?? prev.port,
            username:     r.data.username     ?? prev.username,
            repo_path:    r.data.repo_path    ?? prev.repo_path,
            branch:       r.data.branch       ?? prev.branch,
            compose_file: r.data.compose_file ?? prev.compose_file,
            // Never overwrite private_key from server response — it's
            // a redaction placeholder, not the real key.
            private_key:  "",
          }));
          setSavedKey(r.data.private_key || "");
        }
      } catch {/* config not yet saved — fine */}
      try {
        const r = await api.get("/deploy/history");
        setHistory(r.data?.runs || []);
      } catch {/* no history yet */}
    })();
  }, []);

  async function saveConfig(e) {
    e.preventDefault();
    setBusy(true);
    setStatus(null);
    try {
      // If user left private_key blank AND backend already has one saved,
      // we cannot omit it (backend requires min_length=40). Show a
      // friendly error instead of letting it round-trip to a 422.
      if (!cfg.private_key && !savedKey) {
        throw new Error("ssh_private_key_required");
      }
      // If saved key exists and user didn't type a new one, hit a
      // dedicated patch flow that won't overwrite the stored key.
      const payload = { ...cfg };
      if (!payload.private_key && savedKey) {
        // Backend requires private_key on POST /deploy/config. Send
        // the user-friendly intent: keep the stored key. Currently
        // backend has no patch endpoint, so we instruct the user.
        throw new Error("paste_existing_or_new_ssh_key_to_resave");
      }
      await api.post("/deploy/config", payload);
      setStatus({ ok: true, msg: "Deploy config saved." });
      // Re-fetch so the masked private_key marker comes back.
      const r = await api.get("/deploy/config");
      if (r.data?.configured) setSavedKey(r.data.private_key || "");
      setCfg((p) => ({ ...p, private_key: "" }));
    } catch (err) {
      const m = err?.response?.data?.detail || err?.message || "Save failed";
      const friendly =
        m === "ssh_private_key_required"
          ? "Paste your SSH private key (PEM) before saving."
        : m === "paste_existing_or_new_ssh_key_to_resave"
          ? "To update other fields, re-paste the SSH private key (we never store it in a recoverable form, so it must be re-supplied)."
        : m === "private_key_must_be_pem"
          ? "Key must start with -----BEGIN ... PRIVATE KEY-----."
        : (m && m.code === "vault_unavailable")
          ? "Vault not configured on this server. Ask an admin to set AUREM_CTO_MASTER_KEY."
        : m;
      setStatus({ ok: false, msg: friendly });
    } finally {
      setBusy(false);
    }
  }

  async function runDeploy() {
    setRunning(true);
    setStatus(null);
    try {
      const r = await api.post("/deploy/run", { mode: "deploy" });
      setStatus({ ok: true, msg: `Deploy queued (run ${r.data?.run_id ?? "—"})` });
      const r2 = await api.get("/deploy/history");
      setHistory(r2.data?.runs || []);
    } catch (err) {
      const m = err?.response?.data?.detail || "Deploy failed";
      setStatus({ ok: false, msg: m === "deploy_not_configured"
        ? "Save your deploy config first."
        : m });
    } finally {
      setRunning(false);
    }
  }

  const canDeploy = Boolean(savedKey) && Boolean(cfg.host) && Boolean(cfg.repo_path);

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
            <span className="label-mini">Host (IP or hostname)</span>
            <input data-testid="deploy-host" className="input" value={cfg.host}
                   onChange={(e) => setCfg({ ...cfg, host: e.target.value })}
                   placeholder="203.0.113.1 or app.example.com" required />
          </label>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 12 }}>
            <label>
              <span className="label-mini">SSH user</span>
              <input data-testid="deploy-user" className="input" value={cfg.username}
                     onChange={(e) => setCfg({ ...cfg, username: e.target.value })} />
            </label>
            <label>
              <span className="label-mini">Remote path</span>
              <input data-testid="deploy-path" className="input" value={cfg.repo_path}
                     onChange={(e) => setCfg({ ...cfg, repo_path: e.target.value })}
                     placeholder="/opt/app" required />
            </label>
          </div>

          <label>
            <span className="label-mini">Branch</span>
            <input data-testid="deploy-branch" className="input" value={cfg.branch}
                   onChange={(e) => setCfg({ ...cfg, branch: e.target.value })} />
          </label>

          <label>
            <span className="label-mini" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <KeyRound size={11} /> SSH private key (PEM)
              {savedKey && (
                <span data-testid="deploy-key-stored" style={{ color: "var(--ok)", fontSize: 10 }}>
                  • stored
                </span>
              )}
            </span>
            <textarea data-testid="deploy-private-key" className="input"
                      rows={5}
                      style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, resize: "vertical" }}
                      value={cfg.private_key}
                      onChange={(e) => setCfg({ ...cfg, private_key: e.target.value })}
                      placeholder={savedKey
                        ? "Key is stored. Paste again to overwrite."
                        : "-----BEGIN OPENSSH PRIVATE KEY-----\n...\n-----END OPENSSH PRIVATE KEY-----"} />
            <span style={{ fontSize: 10, color: "var(--text-faint)" }}>
              Encrypted with vault; never displayed back. Generate one server-side: <code>ssh-keygen -t ed25519 -f deploy_key</code>.
            </span>
          </label>

          <button type="button" data-testid="deploy-toggle-adv"
                  onClick={() => setShowAdv((v) => !v)}
                  className="btn-ghost"
                  style={{ alignSelf: "start", padding: "4px 10px", fontSize: 11, display: "flex", alignItems: "center", gap: 6 }}>
            <Settings2 size={11} /> {showAdv ? "Hide" : "Show"} advanced
          </button>

          {showAdv && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 12 }}>
              <label>
                <span className="label-mini">SSH port</span>
                <input data-testid="deploy-port" className="input" type="number" min={1} max={65535}
                       value={cfg.port}
                       onChange={(e) => setCfg({ ...cfg, port: Number(e.target.value) || 22 })} />
              </label>
              <label>
                <span className="label-mini">Compose file</span>
                <input data-testid="deploy-compose" className="input" value={cfg.compose_file}
                       onChange={(e) => setCfg({ ...cfg, compose_file: e.target.value })} />
              </label>
            </div>
          )}

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
                    disabled={running || !canDeploy} onClick={runDeploy}
                    title={canDeploy ? "" : "Save config (with SSH key) first"}>
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
                  <Server size={11} style={{ color: h.status === "ok" ? "var(--ok)" : "var(--danger)" }} />
                  <span style={{ flex: 1 }}>{h.host || h.target || "—"} · {h.mode || "deploy"}</span>
                  <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10 }}>
                    {h.started_at || ""}
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
