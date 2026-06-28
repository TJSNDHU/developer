/**
 * ShipConfirmModal.jsx — Iter 212m-88
 *
 * 3-phase Ship-via-CTO modal:
 *
 *   1) confirm  — files changed list + Vanguard preflight pill +
 *                 Cancel / Ship it buttons (the original modal)
 *   2) shipping — task in flight: live stage badge, latest worker
 *                 steps, link to GitHub PR/commit as soon as available,
 *                 streaming Vanguard scan (calls /tasks/{id}/scan when
 *                 status flips to "done")
 *   3) shipped  — green success card with commit SHA link, post-push
 *                 Vanguard summary, **Rollback** button (POST /tasks/
 *                 {id}/rollback). On rollback success, modal flips to
 *                 "reverted" state.
 *
 * Trigger (unchanged):
 *   window.dispatchEvent(new CustomEvent("aurem:open-ship-modal", {
 *     detail: { files, vanguard, onShip, project },
 *   }));
 *
 * `onShip` MUST return a Promise that resolves to `{ task_id }` (or
 * raise). When it resolves we move to the shipping phase and start
 * polling `/cto/tasks/{task_id}` every 1.5 s until terminal state.
 */
import React, { useEffect, useRef, useState } from "react";
import {
  CheckCircle2, FileText, X, Loader2, ExternalLink, Undo2, ShieldAlert,
} from "lucide-react";
import { api } from "../lib/api";

const POLL_MS = 1500;
const TERMINAL = new Set(["done", "failed"]);

const STAGE_LABEL = {
  queued:  "Queued…",
  pulling: "Cloning repo…",
  reading: "Reading files…",
  fixing:  "AI thinking…",
  pushing: "Writing & pushing…",
  done:    "Pushed",
  failed:  "Failed",
};

export default function ShipConfirmModal() {
  const [open, setOpen]         = useState(false);
  const [phase, setPhase]       = useState("confirm");   // confirm | shipping | shipped | reverted | error
  const [files, setFiles]       = useState([]);
  const [vanguard, setVan]      = useState({ critical: 0 });
  const [onShip, setOnShip]     = useState(() => async () => ({}));
  const [project, setProject]   = useState(null);
  const [task, setTask]         = useState(null);
  const [taskId, setTaskId]     = useState(null);
  const [scan, setScan]         = useState(null);
  const [rbBusy, setRbBusy]     = useState(false);
  const [err, setErr]           = useState("");
  const pollRef = useRef(null);

  // open via event
  useEffect(() => {
    const handler = (e) => {
      const d = e?.detail || {};
      // Iter 212m-106 — Loop engine emits `kind: "shipped" | "failed"`
      // AFTER the GitHub commit completes. Skip the confirm step and
      // jump straight to the post-ship state so the user sees the real
      // commit_sha + html_url. Legacy callers (manual ship from chat
      // bubble) still use the no-kind / "confirm" path below.
      if (d.kind === "shipped") {
        setFiles(Array.isArray(d.files) ? d.files : []);
        setVan({ critical: 0 });
        setOnShip(() => async () => ({}));
        setProject(null);
        setErr("");
        setScan(d.scan || null);
        setTaskId(null);
        setTask({
          status:      "succeeded",
          commit_sha:  d.commit_sha || "",
          full_sha:    d.full_sha || "",
          html_url:    d.html_url || "",
          commit_msg:  d.commit_msg || "",
        });
        setPhase("shipped");
        setOpen(true);
        return;
      }
      if (d.kind === "failed") {
        setFiles([]);
        setVan({ critical: 0 });
        setOnShip(() => async () => ({}));
        setProject(null);
        setScan(null);
        setTaskId(null);
        setTask(null);
        setErr(d.error || "Ship failed");
        setPhase("error");
        setOpen(true);
        return;
      }
      setFiles(Array.isArray(d.files) ? d.files : []);
      setVan(d.vanguard || { critical: 0 });
      setOnShip(() => (typeof d.onShip === "function" ? d.onShip : async () => ({})));
      setProject(d.project || null);
      setTask(null); setTaskId(null); setScan(null); setErr("");
      setPhase("confirm");
      setOpen(true);
    };
    window.addEventListener("aurem:open-ship-modal", handler);
    return () => window.removeEventListener("aurem:open-ship-modal", handler);
  }, []);

  // close on Esc (only during non-flying phases — don't lose the polling
  // ref mid-ship by closing the modal)
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape" && phase !== "shipping") closeAll();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, phase]);

  // poll task status while shipping
  useEffect(() => {
    if (phase !== "shipping" || !taskId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await api.get(`/cto/tasks/${taskId}`);
        if (cancelled) return;
        const t = r.data?.task;
        if (t) {
          setTask(t);
          if (TERMINAL.has(t.status)) {
            // pull the post-push Vanguard scan once
            try {
              const sr = await api.get(`/cto/tasks/${taskId}/scan`);
              if (!cancelled) setScan(sr.data?.scan ?? null);
            } catch { /* scan may not be ready — skip */ }
            setPhase(t.status === "done" ? "shipped" : "error");
            return; // stop polling
          }
        }
      } catch { /* keep polling */ }
      if (!cancelled) pollRef.current = setTimeout(tick, POLL_MS);
    };
    pollRef.current = setTimeout(tick, 300);
    return () => {
      cancelled = true;
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [phase, taskId]);

  function closeAll() {
    if (pollRef.current) clearTimeout(pollRef.current);
    setOpen(false);
  }

  async function handleShip() {
    if (phase !== "confirm") return;
    setPhase("shipping");
    setErr("");
    try {
      const r = await onShip();
      const tid = r?.task_id || r?.taskId || null;
      if (!tid) {
        // onShip didn't expose a task_id — assume fire-and-forget (legacy);
        // we still leave the modal in shipping/info mode for ~2 s, then close.
        setTimeout(closeAll, 2500);
        return;
      }
      setTaskId(tid);
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || "Ship failed";
      setErr(msg); setPhase("error");
    }
  }

  async function handleRollback() {
    if (!taskId || rbBusy) return;
    const ok = window.confirm("Rollback this ship? This reverts the commit on GitHub.");
    if (!ok) return;
    setRbBusy(true);
    try {
      await api.post(`/cto/tasks/${taskId}/rollback`, {});
      // poll for rollback_sha
      const stop = Date.now() + 30_000;
      while (Date.now() < stop) {
        const r = await api.get(`/cto/tasks/${taskId}`);
        const t = r.data?.task;
        if (t?.rollback_sha) { setTask(t); setPhase("reverted"); break; }
        await new Promise((res) => setTimeout(res, 1500));
      }
    } catch (e) {
      setErr(e?.response?.data?.detail || "Rollback failed");
    } finally { setRbBusy(false); }
  }

  if (!open) return null;

  // ── shared frame ────────────────────────────────────────────────────
  const sha    = task?.commit_sha;
  const owner  = project?.github_owner || task?.repo_owner;
  const repo   = project?.github_repo  || task?.repo_name;
  const branch = task?.branch || project?.branch;
  // Iter 212m-107 — prefer the html_url GitHub returns directly (loop
  // ship flow) over the reconstructed URL. The full SHA in the URL is
  // exact whereas owner/repo lookups can be stale post-rename.
  const prUrl  = task?.html_url || task?.pr_url
    || (sha && owner && repo
        ? `https://github.com/${owner}/${repo}/commit/${task?.full_sha || sha}`
        : null);

  // post-push scan summary helpers
  const scanFindings = Array.isArray(scan?.findings) ? scan.findings : [];
  const scanCritical = scanFindings.filter(
    (f) => (f.severity || "").toLowerCase() === "critical",
  ).length;
  const scanClean = scanFindings.length === 0;

  return (
    <div
      data-testid="ship-modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget && phase !== "shipping") closeAll();
      }}
      style={{
        position: "fixed", inset: 0, zIndex: 100,
        background: "rgba(0,0,0,0.78)", backdropFilter: "blur(6px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        data-testid="ship-modal"
        data-phase={phase}
        style={{
          width: "min(520px, 100%)", background: "#161616",
          border: "1px solid #222", borderRadius: 12, padding: 24,
          boxShadow: "0 24px 60px rgba(0,0,0,0.6)",
          fontFamily: "'Jost', system-ui, sans-serif",
          color: "#F5F5F5",
        }}
      >
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          marginBottom: 18,
        }}>
          <h2 style={{
            fontSize: 18, fontWeight: 700, margin: 0, letterSpacing: "-0.01em",
            display: "flex", alignItems: "center", gap: 10,
          }}>
            {phase === "shipping" && (
              <Loader2 size={16} className="animate-spin"
                style={{ color: "#FF6608", animation: "spin 1s linear infinite" }} />
            )}
            {phase === "confirm"  && "Ship via CTO"}
            {phase === "shipping" && "Shipping…"}
            {phase === "shipped"  && "Shipped"}
            {phase === "reverted" && "Reverted"}
            {phase === "error"    && "Ship failed"}
          </h2>
          {phase !== "shipping" && (
            <button data-testid="ship-modal-close" onClick={closeAll}
              aria-label="Close"
              style={{
                background: "transparent", border: 0, color: "#8A8A8A",
                cursor: "pointer", padding: 4, borderRadius: 4,
              }}><X size={16} /></button>
          )}
        </div>

        {/* ── files list (always shown in confirm; collapsed in later phases) ── */}
        {phase === "confirm" && (
          <div style={{ marginBottom: 16 }}>
            <div style={{
              fontSize: 10, color: "#8A8A8A", textTransform: "uppercase",
              letterSpacing: "0.12em", marginBottom: 8,
              fontFamily: "'JetBrains Mono', monospace",
            }}>Files changed ({files.length})</div>
            {files.length === 0 ? (
              <div style={{ fontSize: 12, color: "#666", fontStyle: "italic" }}>
                No changes detected
              </div>
            ) : (
              <ul style={{ listStyle: "none", padding: 0, margin: 0,
                           display: "grid", gap: 6 }}>
                {files.slice(0, 8).map((f, i) => (
                  <li key={i} style={{
                    display: "flex", alignItems: "center", gap: 8,
                    padding: "8px 10px", borderRadius: 6,
                    background: "#0A0A0A", border: "1px solid #222",
                    fontSize: 12,
                  }}>
                    <FileText size={12} style={{ color: "#FF6608", flex: "0 0 auto" }} />
                    <span style={{
                      flex: 1, fontFamily: "'JetBrains Mono', monospace",
                      color: "#F5F5F5", overflow: "hidden",
                      textOverflow: "ellipsis", whiteSpace: "nowrap",
                    }}>{f.path || f.file || "(unnamed)"}</span>
                    {(f.added != null || f.removed != null) && (
                      <span style={{ fontSize: 11, fontFamily: "'JetBrains Mono', monospace" }}>
                        <span style={{ color: "#22C55E" }}>+{f.added ?? 0}</span>
                        <span style={{ color: "#8A8A8A" }}>{" / "}</span>
                        <span style={{ color: "#EF4444" }}>−{f.removed ?? 0}</span>
                      </span>
                    )}
                  </li>
                ))}
                {files.length > 8 && (
                  <li style={{ fontSize: 11, color: "#8A8A8A", paddingLeft: 10 }}>
                    +{files.length - 8} more
                  </li>
                )}
              </ul>
            )}
          </div>
        )}

        {/* ── live task progress (shipping phase) ── */}
        {phase === "shipping" && (
          <div data-testid="ship-modal-progress" style={{
            padding: "14px 16px", borderRadius: 8,
            background: "#0A0A0A", border: "1px solid #222",
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 12, marginBottom: 16,
          }}>
            <div style={{
              display: "flex", alignItems: "center", gap: 8,
              color: "#FF6608", fontWeight: 600,
            }}>
              <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} />
              <span data-testid="ship-modal-stage">
                {STAGE_LABEL[task?.status] || "Queued…"}
              </span>
              {taskId && (
                <span style={{ marginLeft: "auto", color: "#666", fontSize: 10 }}>
                  {taskId.slice(0, 8)}…
                </span>
              )}
            </div>
            {(task?.steps || []).slice(-3).map((s, i) => (
              <div key={i} style={{
                marginTop: 6, fontSize: 11,
                color: s.status === "error" ? "#EF4444" : "#8A8A8A",
              }}>{s.step}</div>
            ))}
            {prUrl && (
              <a href={prUrl} target="_blank" rel="noreferrer"
                data-testid="ship-modal-pr-url"
                style={{
                  display: "inline-flex", alignItems: "center", gap: 6,
                  marginTop: 10, padding: "5px 10px", borderRadius: 4,
                  color: "#FF6608", textDecoration: "none",
                  background: "rgba(255,102,8,0.08)",
                  border: "1px solid rgba(255,102,8,0.3)",
                  fontSize: 11,
                }}>
                <ExternalLink size={11} /> View on GitHub
              </a>
            )}
          </div>
        )}

        {/* ── shipped success block ── */}
        {(phase === "shipped" || phase === "reverted") && (
          <div data-testid="ship-modal-success" style={{
            padding: "14px 16px", borderRadius: 8, marginBottom: 16,
            background: phase === "reverted"
              ? "#161616"
              : "rgba(34,197,94,0.06)",
            border: `1px solid ${phase === "reverted" ? "#333" : "rgba(34,197,94,0.3)"}`,
            fontFamily: "'JetBrains Mono', monospace", fontSize: 12,
          }}>
            <div style={{
              display: "flex", alignItems: "center", gap: 8,
              color: phase === "reverted" ? "#8A8A8A" : "#22C55E",
              fontWeight: 600, marginBottom: 10,
            }}>
              <CheckCircle2 size={14} />
              {phase === "reverted" ? "Reverted on GitHub" : "Pushed to GitHub"}
              {sha && (
                <span
                  data-testid="ship-modal-sha-chip"
                  title={task?.full_sha || sha}
                  onClick={() => {
                    // Iter 212m-107 — click to copy full SHA to clipboard.
                    try { navigator.clipboard?.writeText(task?.full_sha || sha); } catch { /* ignore */ }
                  }}
                  style={{
                    marginLeft: "auto", padding: "2px 8px", borderRadius: 999,
                    background: "rgba(255,102,8,0.10)",
                    border: "1px solid rgba(255,102,8,0.32)",
                    color: "#FF6608",
                    fontSize: 10, fontWeight: 700, letterSpacing: "0.04em",
                    cursor: "pointer", userSelect: "all",
                  }}
                >
                  {sha.slice(0, 7)}
                </span>
              )}
            </div>
            {prUrl && (
              <a
                href={prUrl} target="_blank" rel="noreferrer"
                data-testid="ship-modal-view-on-github"
                style={{
                  display: "inline-flex", alignItems: "center", gap: 8,
                  padding: "8px 14px", borderRadius: 8,
                  background: "#FF6608", color: "#0A0A0A",
                  textDecoration: "none", fontSize: 12, fontWeight: 700,
                  letterSpacing: "0.02em",
                  boxShadow: "0 0 20px -6px rgba(255,102,8,0.55)",
                  transition: "transform 100ms ease, box-shadow 200ms ease",
                }}
              >
                <ExternalLink size={12} strokeWidth={2.5} /> View on GitHub
              </a>
            )}
            {branch && (
              <div style={{ marginTop: 8, fontSize: 11, color: "#8A8A8A" }}>
                branch · {branch}
              </div>
            )}
          </div>
        )}

        {/* ── post-push Vanguard scan (streaming) ── */}
        {(phase === "shipped" || phase === "shipping") && taskId && (
          <div data-testid="ship-modal-vanguard" style={{
            display: "inline-flex", alignItems: "center", gap: 8,
            padding: "8px 12px", borderRadius: 999,
            background: scan === null
              ? "rgba(255,102,8,0.08)"
              : (scanClean
                  ? "rgba(34,197,94,0.12)"
                  : "rgba(239,68,68,0.12)"),
            color: scan === null
              ? "#FF6608"
              : (scanClean ? "#22C55E" : "#EF4444"),
            fontSize: 11, fontWeight: 600,
            fontFamily: "'JetBrains Mono', monospace",
            letterSpacing: "0.05em", marginBottom: 16,
          }}>
            {scan === null ? (
              <>
                <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} />
                Vanguard scanning…
              </>
            ) : scanClean ? (
              <>
                <CheckCircle2 size={12} />
                Vanguard clean · {scanFindings.length} findings
              </>
            ) : (
              <>
                <ShieldAlert size={12} />
                Vanguard flagged · {scanCritical} critical, {scanFindings.length} total
              </>
            )}
          </div>
        )}

        {/* ── confirm-phase Vanguard preflight badge ── */}
        {phase === "confirm" && (
          <div data-testid="ship-vanguard-badge" style={{
            display: "inline-flex", alignItems: "center", gap: 8,
            padding: "8px 12px", borderRadius: 999,
            background: vanguard?.critical
              ? "rgba(239,68,68,0.12)"
              : "rgba(34,197,94,0.12)",
            color: vanguard?.critical ? "#EF4444" : "#22C55E",
            fontSize: 11, fontWeight: 600,
            fontFamily: "'JetBrains Mono', monospace",
            letterSpacing: "0.05em", marginBottom: 22,
          }}>
            <CheckCircle2 size={12} />
            Vanguard {vanguard?.critical ? `flagged ${vanguard.critical} critical` : "clean"} ·{" "}
            {vanguard?.critical ?? 0} critical
          </div>
        )}

        {/* ── error ── */}
        {(phase === "error" || err) && (
          <div data-testid="ship-modal-error" style={{
            padding: "10px 14px", borderRadius: 6, marginBottom: 16,
            background: "rgba(239,68,68,0.08)",
            border: "1px solid rgba(239,68,68,0.3)",
            color: "#EF4444", fontSize: 12,
          }}>⚠ {err || "Ship failed"}</div>
        )}

        {/* ── actions ── */}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
          {phase === "confirm" && (
            <>
              <button data-testid="ship-modal-cancel" onClick={closeAll}
                style={{
                  padding: "9px 16px", fontSize: 13, fontWeight: 600,
                  background: "transparent", color: "#F5F5F5",
                  border: "1px solid #333", borderRadius: 6,
                  cursor: "pointer",
                }}>Cancel</button>
              <button data-testid="ship-modal-confirm" onClick={handleShip}
                style={{
                  padding: "9px 18px", fontSize: 13, fontWeight: 700,
                  background: "#FF6608", color: "#0A0A0A",
                  border: 0, borderRadius: 6, cursor: "pointer",
                }}>Ship it</button>
            </>
          )}
          {phase === "shipping" && (
            <button data-testid="ship-modal-minimize" onClick={closeAll}
              style={{
                padding: "9px 16px", fontSize: 13, fontWeight: 600,
                background: "transparent", color: "#8A8A8A",
                border: "1px solid #333", borderRadius: 6, cursor: "pointer",
              }}>Run in background</button>
          )}
          {phase === "shipped" && (
            <>
              <button data-testid="ship-modal-rollback" onClick={handleRollback}
                disabled={rbBusy}
                style={{
                  padding: "9px 14px", fontSize: 13, fontWeight: 600,
                  background: "transparent", color: "#EF4444",
                  border: "1px solid rgba(239,68,68,0.4)", borderRadius: 6,
                  cursor: rbBusy ? "wait" : "pointer",
                  display: "inline-flex", alignItems: "center", gap: 6,
                  opacity: rbBusy ? 0.6 : 1,
                }}>
                <Undo2 size={12} /> {rbBusy ? "Rolling back…" : "Rollback"}
              </button>
              <button data-testid="ship-modal-done" onClick={closeAll}
                style={{
                  padding: "9px 18px", fontSize: 13, fontWeight: 700,
                  background: "#FF6608", color: "#0A0A0A",
                  border: 0, borderRadius: 6, cursor: "pointer",
                }}>Done</button>
            </>
          )}
          {(phase === "reverted" || phase === "error") && (
            <button data-testid="ship-modal-close-final" onClick={closeAll}
              style={{
                padding: "9px 18px", fontSize: 13, fontWeight: 700,
                background: "#FF6608", color: "#0A0A0A",
                border: 0, borderRadius: 6, cursor: "pointer",
              }}>Close</button>
          )}
        </div>
      </div>
    </div>
  );
}
