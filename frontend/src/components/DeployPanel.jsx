/**
 * DeployPanel.jsx — BYOH (Bring-Your-Own-Host) deployment console.
 *
 * Four states (Iter 212m-9):
 *   1. no_config   — SSH config not saved → render setup form
 *   2. idle        — Config saved, no run in flight → "Deploy now" + run history
 *   3. deploying   — A run is in flight (or selected) → polled live logs
 *   4. done/failed — Run finished → final status + tail of logs + "deploy again"
 *
 * Backend endpoints used (all under /api/aurem-dev):
 *   GET    /deploy/config/{project_id}     (hybrid fallback to user-level)
 *   POST   /deploy/config                  (save / overwrite SSH config)
 *   DELETE /deploy/config                  (forget the saved SSH key)
 *   POST   /deploy/run                     (kick off a deploy / dry_run / rollback)
 *   GET    /deploy/runs?project_id=…       (run history, filtered when scoped)
 *   GET    /deploy/runs/{run_id}/logs?since=N  (incremental log tail)
 */
import React, { useEffect, useRef, useState, useCallback } from "react";
import {
  Loader2, RefreshCw, Trash2, ServerCog, Play, RotateCcw, CheckCircle2,
  XCircle, Clock, ChevronRight,
} from "lucide-react";
import { api } from "../lib/api";
import { toast } from "./Toast";

const POLL_INTERVAL_MS = 1500;
const FINISHED = new Set(["ok", "failed", "timeout"]);

const STATUS_PILL = {
  ok:      { label: "ok",       color: "var(--accent-2)", Icon: CheckCircle2 },
  failed:  { label: "failed",   color: "var(--danger)",   Icon: XCircle },
  timeout: { label: "timeout",  color: "var(--danger)",   Icon: XCircle },
  running: { label: "running",  color: "var(--accent)",   Icon: Loader2 },
};

function StatusPill({ status, size = 11 }) {
  const cfg = STATUS_PILL[status] || { label: status || "—", color: "var(--text-faint)", Icon: Clock };
  const Icon = cfg.Icon;
  const spinning = status === "running";
  return (
    <span
      data-testid={`deploy-status-pill-${status || "unknown"}`}
      style={{
        display: "inline-flex", alignItems: "center", gap: 4,
        padding: "2px 8px", borderRadius: 4,
        background: "var(--panel-2)",
        color: cfg.color,
        border: `1px solid ${cfg.color}`,
        fontSize: 10, fontWeight: 600,
        fontFamily: "'JetBrains Mono', monospace",
        letterSpacing: "0.05em", textTransform: "uppercase",
      }}
    >
      <Icon size={size} style={spinning ? { animation: "spin 1s linear infinite" } : undefined} />
      {cfg.label}
    </span>
  );
}

function ConfigForm({ projectId, existing, onSaved, onCancel }) {
  const [host, setHost] = useState(existing?.host || "");
  const [port, setPort] = useState(existing?.port || 22);
  const [username, setUsername] = useState(existing?.username || "root");
  const [privateKey, setPrivateKey] = useState("");
  const [repoPath, setRepoPath] = useState(existing?.repo_path || "");
  const [branch, setBranch] = useState(existing?.branch || "main");
  const [composeFile, setComposeFile] = useState(existing?.compose_file || "docker-compose.yml");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const save = async () => {
    setErr("");
    if (privateKey.trim().length < 40 || !privateKey.includes("BEGIN")) {
      setErr("Paste a PEM-formatted private key (BEGIN … PRIVATE KEY).");
      return;
    }
    setSaving(true);
    try {
      await api.post("/deploy/config", {
        host: host.trim(),
        port: Number(port) || 22,
        username: username.trim() || "root",
        private_key: privateKey,
        repo_path: repoPath.trim(),
        branch: branch.trim() || "main",
        compose_file: composeFile.trim() || "docker-compose.yml",
        project_id: projectId || "",
      });
      toast({ message: "SSH config saved.", kind: "success" });
      onSaved?.();
    } catch (e) {
      const msg = e?.response?.data?.detail?.msg || e?.response?.data?.detail
        || e?.message || "Failed to save config.";
      setErr(typeof msg === "string" ? msg : JSON.stringify(msg));
    } finally {
      setSaving(false);
    }
  };

  const inputStyle = {
    width: "100%", padding: "6px 8px",
    background: "var(--bg)", color: "var(--text)",
    border: "1px solid var(--border)",
    borderRadius: 4, fontSize: 12,
    fontFamily: "'JetBrains Mono', monospace",
  };
  const labelStyle = {
    fontSize: 10, color: "var(--text-faint)", letterSpacing: "0.1em",
    textTransform: "uppercase", marginBottom: 4, display: "block",
  };

  return (
    <div data-testid="deploy-config-form" style={{ padding: 16, overflow: "auto", height: "100%" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
        <ServerCog size={14} color="var(--accent-2)" />
        <h3 style={{
          margin: 0, fontSize: 13, color: "var(--text)",
          letterSpacing: "0.1em", textTransform: "uppercase",
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          {existing ? "Update SSH config" : "Connect your VPS"}
        </h3>
      </div>

      <p style={{ fontSize: 11, color: "var(--text-dim)", lineHeight: 1.55, marginBottom: 14 }}>
        Bring-Your-Own-Host: paste an SSH private key, set the repo path, and
        ORA will run <code style={{ background: "var(--panel-2)", padding: "1px 4px", borderRadius: 3 }}>git pull && docker compose up</code> on
        your server. Key is encrypted at rest with your vault key — never returned.
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 10, marginBottom: 10 }}>
        <div>
          <label style={labelStyle}>Host</label>
          <input
            data-testid="deploy-cfg-host"
            value={host} onChange={(e) => setHost(e.target.value)}
            placeholder="my-vps.example.com" style={inputStyle}
          />
        </div>
        <div>
          <label style={labelStyle}>Port</label>
          <input
            data-testid="deploy-cfg-port"
            type="number" value={port} onChange={(e) => setPort(e.target.value)}
            style={inputStyle}
          />
        </div>
      </div>

      <div style={{ marginBottom: 10 }}>
        <label style={labelStyle}>SSH username</label>
        <input
          data-testid="deploy-cfg-username"
          value={username} onChange={(e) => setUsername(e.target.value)}
          placeholder="root" style={inputStyle}
        />
      </div>

      <div style={{ marginBottom: 10 }}>
        <label style={labelStyle}>
          Private key (PEM){existing ? " — leave blank to keep existing key" : ""}
        </label>
        <textarea
          data-testid="deploy-cfg-private-key"
          value={privateKey}
          onChange={(e) => setPrivateKey(e.target.value)}
          placeholder={"-----BEGIN OPENSSH PRIVATE KEY-----\n…\n-----END OPENSSH PRIVATE KEY-----"}
          rows={6}
          style={{ ...inputStyle, fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}
        />
      </div>

      <div style={{ marginBottom: 10 }}>
        <label style={labelStyle}>Repo path on the server</label>
        <input
          data-testid="deploy-cfg-repo-path"
          value={repoPath} onChange={(e) => setRepoPath(e.target.value)}
          placeholder="/srv/myapp" style={inputStyle}
        />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 14 }}>
        <div>
          <label style={labelStyle}>Branch</label>
          <input
            data-testid="deploy-cfg-branch"
            value={branch} onChange={(e) => setBranch(e.target.value)}
            placeholder="main" style={inputStyle}
          />
        </div>
        <div>
          <label style={labelStyle}>Compose file</label>
          <input
            data-testid="deploy-cfg-compose"
            value={composeFile} onChange={(e) => setComposeFile(e.target.value)}
            placeholder="docker-compose.yml" style={inputStyle}
          />
        </div>
      </div>

      {err && (
        <div data-testid="deploy-cfg-error" style={{
          padding: "6px 10px", marginBottom: 10,
          background: "rgba(239,68,68,0.08)",
          border: "1px solid var(--danger)", borderRadius: 4,
          color: "var(--danger)", fontSize: 11,
        }}>⚠ {err}</div>
      )}

      <div style={{ display: "flex", gap: 8 }}>
        <button
          data-testid="deploy-cfg-save"
          onClick={save}
          disabled={saving || !host || !repoPath || (!existing && !privateKey)}
          style={{
            padding: "8px 16px", fontSize: 12, fontWeight: 600,
            background: "var(--accent-2)", color: "var(--bg)",
            border: "1px solid var(--accent-2)", borderRadius: 4,
            cursor: saving ? "wait" : "pointer",
            fontFamily: "'JetBrains Mono', monospace",
            letterSpacing: "0.05em",
            opacity: (!host || !repoPath || (!existing && !privateKey)) ? 0.5 : 1,
          }}
        >
          {saving
            ? <><Loader2 size={11} style={{ animation: "spin 1s linear infinite" }} /> saving…</>
            : <>Save config</>}
        </button>
        {existing && onCancel && (
          <button
            data-testid="deploy-cfg-cancel"
            onClick={onCancel}
            className="btn-ghost"
            style={{ padding: "8px 14px", fontSize: 12 }}
          >
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}

function LogStream({ runId, lines, status, headSha, onRerun }) {
  const scrollRef = useRef(null);
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [lines.length]);
  return (
    <div data-testid="deploy-log-stream" style={{
      display: "flex", flexDirection: "column", height: "100%", minHeight: 0,
    }}>
      <div style={{
        padding: "8px 14px", borderBottom: "1px solid var(--border)",
        display: "flex", alignItems: "center", gap: 10,
        background: "var(--bg-elev)",
      }}>
        <StatusPill status={status} />
        <span style={{
          fontSize: 10, color: "var(--text-faint)",
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          run {runId?.slice(0, 8) || "—"}
        </span>
        {headSha && (
          <span data-testid="deploy-head-sha" style={{
            fontSize: 10, color: "var(--accent-2)",
            fontFamily: "'JetBrains Mono', monospace",
          }}>
            @ {headSha.slice(0, 7)}
          </span>
        )}
        {FINISHED.has(status) && onRerun && (
          <button
            data-testid="deploy-rerun-btn"
            onClick={onRerun}
            className="btn-ghost"
            style={{ marginLeft: "auto", padding: "4px 10px", fontSize: 11 }}
          >
            <RefreshCw size={11} /> Deploy again
          </button>
        )}
      </div>
      <pre
        ref={scrollRef}
        data-testid="deploy-log-output"
        style={{
          margin: 0, padding: 14, flex: 1, overflow: "auto",
          fontSize: 11, lineHeight: 1.55,
          fontFamily: "'JetBrains Mono', monospace",
          background: "var(--bg)", color: "var(--text)",
          whiteSpace: "pre-wrap", wordBreak: "break-word",
        }}
      >
        {lines.length === 0
          ? <span style={{ color: "var(--text-faint)" }}>waiting for output…</span>
          : lines.join("\n")}
      </pre>
    </div>
  );
}

function HistoryList({ runs, onSelect, selectedRunId }) {
  if (!runs?.length) {
    return (
      <div
        data-testid="deploy-history-empty"
        style={{ padding: 16, fontSize: 11, color: "var(--text-faint)" }}
      >
        No deploys yet — hit <b>Deploy now</b> above when you&apos;re ready.
      </div>
    );
  }
  return (
    <div data-testid="deploy-history-list">
      <div style={{
        padding: "8px 14px", borderTop: "1px solid var(--border)",
        borderBottom: "1px solid var(--border)",
        background: "var(--bg-elev)",
        fontSize: 10, color: "var(--text-faint)",
        letterSpacing: "0.1em", textTransform: "uppercase",
        fontFamily: "'JetBrains Mono', monospace",
      }}>
        recent runs
      </div>
      <div style={{ overflow: "auto" }}>
        {runs.map((r) => {
          const active = r.run_id === selectedRunId;
          return (
            <button
              key={r.run_id}
              data-testid={`deploy-history-row-${r.run_id}`}
              onClick={() => onSelect(r.run_id)}
              style={{
                display: "flex", alignItems: "center", gap: 10,
                width: "100%", padding: "8px 14px",
                background: active ? "var(--panel-2)" : "transparent",
                color: "var(--text)", border: "none",
                borderBottom: "1px solid var(--border)",
                cursor: "pointer", textAlign: "left",
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 11,
              }}
            >
              <StatusPill status={r.status} />
              <span style={{ color: "var(--text-dim)" }}>{r.mode}</span>
              <span style={{ color: "var(--text-faint)" }}>{r.branch || "main"}</span>
              <span style={{
                marginLeft: "auto", color: "var(--text-faint)", fontSize: 10,
              }}>
                {r.started_at ? new Date(r.started_at).toLocaleString() : ""}
              </span>
              <ChevronRight size={11} color="var(--text-faint)" />
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default function DeployPanel({ activeProject }) {
  const projectId = activeProject?.project_id || "";
  const [phase, setPhase] = useState("loading"); // loading|no_config|idle|deploying|done|editing
  const [config, setConfig] = useState(null);
  const [runs, setRuns] = useState([]);
  const [activeRunId, setActiveRunId] = useState(null);
  const [logs, setLogs] = useState([]);
  const [runStatus, setRunStatus] = useState(null);
  const [headSha, setHeadSha] = useState(null);
  const [cursor, setCursor] = useState(0);
  const [kickoffErr, setKickoffErr] = useState("");
  const [busyMode, setBusyMode] = useState(null); // deploy|dry_run|rollback|null
  const pollRef = useRef(null);

  const stopPoll = () => {
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  };

  const refreshConfig = useCallback(async () => {
    try {
      const path = projectId ? `/deploy/config/${projectId}` : `/deploy/config`;
      const r = await api.get(path);
      setConfig(r.data || {});
      setPhase((cur) => {
        if (cur === "editing") return cur;
        if (!r.data?.configured) return "no_config";
        // If a run is already active we'll stay in deploying — handled below.
        return cur === "deploying" ? cur : "idle";
      });
    } catch (e) {
      console.error("deploy/config GET failed", e);
      setPhase("no_config");
    }
  }, [projectId]);

  const refreshRuns = useCallback(async () => {
    try {
      const r = await api.get(`/deploy/runs`, {
        params: projectId ? { project_id: projectId, limit: 20 } : { limit: 20 },
      });
      const rows = r.data?.runs || [];
      setRuns(rows);
      // Auto-resume a running deploy if we don't already have one selected.
      const running = rows.find((x) => x.status === "running");
      if (running && !activeRunId) {
        setActiveRunId(running.run_id);
        setRunStatus("running");
        setCursor(0);
        setLogs([]);
        setPhase("deploying");
      }
    } catch (e) {
      console.error("deploy/runs GET failed", e);
    }
  }, [projectId, activeRunId]);

  useEffect(() => {
    refreshConfig();
    refreshRuns();
    return () => stopPoll();
  }, [refreshConfig, refreshRuns]);

  // Polling loop for an active run.
  useEffect(() => {
    stopPoll();
    if (!activeRunId) return;
    let cancelled = false;

    const tick = async () => {
      try {
        const r = await api.get(`/deploy/runs/${activeRunId}/logs`, {
          params: { since: cursor },
        });
        if (cancelled) return;
        const newLines = r.data?.lines || [];
        if (newLines.length) {
          setLogs((cur) => [...cur, ...newLines]);
          setCursor(r.data.next_cursor || (cursor + newLines.length));
        }
        const st = r.data?.status;
        setRunStatus(st);
        if (r.data?.head_sha) setHeadSha(r.data.head_sha);
        if (FINISHED.has(st)) {
          setPhase(st === "ok" ? "done" : "failed");
          refreshRuns();
          return; // stop polling
        }
      } catch (e) {
        console.warn("deploy/runs/logs poll error", e);
      }
      if (!cancelled) {
        pollRef.current = setTimeout(tick, POLL_INTERVAL_MS);
      }
    };
    pollRef.current = setTimeout(tick, 0);
    return () => { cancelled = true; stopPoll(); };
  }, [activeRunId, cursor, refreshRuns]);

  const kickoff = async (mode = "deploy") => {
    setKickoffErr("");
    setBusyMode(mode);
    setLogs([]);
    setCursor(0);
    setHeadSha(null);
    try {
      const r = await api.post(`/deploy/run`, {
        mode,
        project_id: projectId || "",
      });
      setActiveRunId(r.data?.run_id);
      setRunStatus("running");
      setPhase("deploying");
    } catch (e) {
      const msg = e?.response?.data?.detail?.msg || e?.response?.data?.detail
        || e?.message || "Failed to start deploy.";
      setKickoffErr(typeof msg === "string" ? msg : JSON.stringify(msg));
    } finally {
      setBusyMode(null);
    }
  };

  const selectRun = async (runId) => {
    setActiveRunId(runId);
    setLogs([]);
    setCursor(0);
    setHeadSha(null);
    setRunStatus("running"); // optimistic until first poll returns real status
    setPhase("deploying");
  };

  const deleteConfig = async () => {
    if (!window.confirm("Forget saved SSH config?")) return;
    try {
      await api.delete(`/deploy/config`, {
        params: projectId ? { project_id: projectId } : {},
      });
      toast({ message: "SSH config removed.", kind: "success" });
      setConfig(null);
      setPhase("no_config");
    } catch (e) {
      toast({ message: e?.message || "Failed to remove config.", kind: "error" });
    }
  };

  // ─── RENDER ───────────────────────────────────────────────────────
  if (phase === "loading") {
    return (
      <div data-testid="deploy-loading" style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: 18, color: "var(--text-faint)", fontSize: 12,
      }}>
        <Loader2 size={13} style={{ animation: "spin 1s linear infinite" }} />
        loading deploy config…
      </div>
    );
  }

  if (phase === "no_config" || phase === "editing") {
    return (
      <ConfigForm
        projectId={projectId}
        existing={phase === "editing" ? config : null}
        onSaved={() => { setPhase("idle"); refreshConfig(); }}
        onCancel={phase === "editing" ? () => setPhase("idle") : undefined}
      />
    );
  }

  // idle | deploying | done | failed
  const isDeploying = phase === "deploying";
  const scope = config?.scope === "project" ? "project" : "default";

  return (
    <div data-testid="deploy-panel" style={{
      display: "flex", flexDirection: "column", height: "100%", minHeight: 0,
    }}>
      {/* Toolbar */}
      <div style={{
        padding: "10px 14px",
        borderBottom: "1px solid var(--border)",
        background: "var(--bg-elev)",
        display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
      }}>
        <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
          <span style={{
            fontSize: 10, letterSpacing: "0.18em",
            color: "var(--text-faint)", textTransform: "uppercase",
            fontFamily: "'JetBrains Mono', monospace",
          }}>
            target ({scope})
          </span>
          <span data-testid="deploy-target" style={{
            fontSize: 12, color: "var(--text)",
            fontFamily: "'JetBrains Mono', monospace",
            whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          }}>
            {config?.username || "root"}@{config?.host || "—"} : {config?.repo_path || "—"} ({config?.branch || "main"})
          </span>
        </div>

        <div style={{ marginLeft: "auto", display: "flex", gap: 6, flexWrap: "wrap" }}>
          <button
            data-testid="deploy-now-btn"
            onClick={() => kickoff("deploy")}
            disabled={isDeploying || busyMode != null}
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "6px 12px",
              background: "var(--accent-2)", color: "var(--bg)",
              border: "1px solid var(--accent-2)", borderRadius: 4,
              fontSize: 12, fontWeight: 600,
              fontFamily: "'JetBrains Mono', monospace",
              cursor: isDeploying ? "wait" : "pointer",
              opacity: isDeploying ? 0.55 : 1,
            }}
          >
            {busyMode === "deploy"
              ? <><Loader2 size={11} style={{ animation: "spin 1s linear infinite" }} /> starting…</>
              : <><Play size={11} /> Deploy now</>}
          </button>
          <button
            data-testid="deploy-dry-run-btn"
            onClick={() => kickoff("dry_run")}
            disabled={isDeploying || busyMode != null}
            className="btn-ghost"
            style={{ padding: "6px 10px", fontSize: 11 }}
          >
            {busyMode === "dry_run"
              ? <><Loader2 size={11} style={{ animation: "spin 1s linear infinite" }} /> dry…</>
              : "Dry run"}
          </button>
          <button
            data-testid="deploy-rollback-btn"
            onClick={() => {
              if (window.confirm("Rollback last commit on remote and redeploy?")) {
                kickoff("rollback");
              }
            }}
            disabled={isDeploying || busyMode != null}
            className="btn-ghost"
            style={{ padding: "6px 10px", fontSize: 11 }}
          >
            <RotateCcw size={11} /> Rollback
          </button>
          <button
            data-testid="deploy-edit-cfg-btn"
            onClick={() => setPhase("editing")}
            className="btn-ghost"
            style={{ padding: "6px 10px", fontSize: 11 }}
            title="Edit SSH config"
          >
            <ServerCog size={11} /> Edit
          </button>
          <button
            data-testid="deploy-delete-cfg-btn"
            onClick={deleteConfig}
            className="btn-ghost"
            style={{ padding: "6px 10px", fontSize: 11, color: "var(--danger)" }}
            title="Remove saved SSH config"
          >
            <Trash2 size={11} />
          </button>
        </div>
      </div>

      {kickoffErr && (
        <div
          data-testid="deploy-kickoff-error"
          style={{
            padding: "8px 14px",
            background: "rgba(239,68,68,0.08)",
            borderBottom: "1px solid var(--danger)",
            color: "var(--danger)", fontSize: 11,
          }}
        >⚠ {kickoffErr}</div>
      )}

      {/* Body: split — log stream on top (if any), history below */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
        {activeRunId ? (
          <div style={{ flex: 1, minHeight: 0, borderBottom: "1px solid var(--border)" }}>
            <LogStream
              runId={activeRunId}
              lines={logs}
              status={runStatus}
              headSha={headSha}
              onRerun={() => kickoff("deploy")}
            />
          </div>
        ) : (
          <div
            data-testid="deploy-idle-hero"
            style={{
              padding: 22, fontSize: 12, color: "var(--text-dim)",
              lineHeight: 1.6,
              borderBottom: "1px solid var(--border)",
            }}
          >
            <div style={{ fontSize: 13, color: "var(--text)", marginBottom: 6 }}>
              Ready to ship to <b style={{ color: "var(--accent-2)" }}>{config?.host}</b>.
            </div>
            Hit <b>Deploy now</b> to <code>git pull && docker compose up -d</code> on
            your server. Use <b>Dry run</b> first if this is a production project —
            it validates auth + compose without restarting containers.
          </div>
        )}
        <HistoryList
          runs={runs}
          onSelect={selectRun}
          selectedRunId={activeRunId}
        />
      </div>
    </div>
  );
}
