/**
 * NewUserWizard.jsx — Onboarding wizard for fresh signups (0 projects).
 *
 *   Step 1  Connect repo     → POST /cto/projects/add
 *   Step 2  First task brief → POST /cto/tasks/submit
 *   Step 3  Live worker tape → <TaskLiveTape /> driven by /cto/tasks/{id}/stream
 *
 * Dismiss rules:
 *   • localStorage["aurem_wizard_dismissed"] = "1" → never show again
 *   • Triggered when projects.length === 0 AND the flag is unset
 *
 * Iter 73 Task 3.
 */
import React, { useState } from "react";
import { Loader2, X, ArrowRight, GitBranch } from "lucide-react";
import { api } from "../lib/api";
import TaskLiveTape from "./TaskLiveTape";
import { setActiveProjectId } from "./TabBar";

const DISMISS_KEY = "aurem_wizard_dismissed";
const REPO_RX = /^(https?:\/\/)?(www\.)?github\.com\/[\w.-]+\/[\w.-]+\/?$/i;
const TASK_HINT =
  "Be specific. e.g. \"Add a dark-mode toggle to the navbar in " +
  "components/Navbar.jsx — use localStorage to persist the choice.\"";

export function isWizardDismissed() {
  try { return localStorage.getItem(DISMISS_KEY) === "1"; }
  catch { return true; }
}
export function dismissWizard() {
  try { localStorage.setItem(DISMISS_KEY, "1"); } catch { /* private mode */ }
}

export default function NewUserWizard({ onComplete }) {
  const [step, setStep]         = useState(1);
  const [repoUrl, setRepoUrl]   = useState("");
  const [branch, setBranch]     = useState("main");
  const [task, setTask]         = useState("");
  const [projectId, setProject] = useState(null);
  const [taskId, setTaskId]     = useState(null);
  const [busy, setBusy]         = useState(false);
  const [err, setErr]           = useState("");

  function close() {
    dismissWizard();
    onComplete?.();
  }
  function goDashboard() {
    if (projectId) setActiveProjectId(projectId);
    close();
  }

  async function submitRepo(e) {
    e?.preventDefault();
    setErr("");
    if (!REPO_RX.test(repoUrl.trim())) {
      setErr("Use a real GitHub repo URL — github.com/owner/repo");
      return;
    }
    setBusy(true);
    try {
      const parts = repoUrl.trim().replace(/\/$/, "").split("/");
      const name = parts[parts.length - 1].replace(/\.git$/, "");
      const r = await api.post("/cto/projects/add", {
        name, github_url: repoUrl.trim(), branch: branch.trim() || "main",
      });
      setProject(r.data?.project_id);
      setStep(2);
    } catch (e2) {
      const msg = e2?.response?.data?.detail || e2?.message || "Could not connect repo.";
      setErr(msg);
      // 400 from /projects/add when GitHub isn't connected → push them to Settings.
      if (/github not connected/i.test(msg)) {
        setErr("GitHub isn't connected. Skip to dashboard, then open Settings → Connect GitHub.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function submitTask(e) {
    e?.preventDefault();
    setErr("");
    if (task.trim().length < 12) {
      setErr("Give ORA a bit more detail (12+ characters).");
      return;
    }
    setBusy(true);
    try {
      const r = await api.post("/cto/tasks/submit", {
        project_id: projectId, task: task.trim(), files: [], context: "",
      });
      setTaskId(r.data?.task_id);
      setActiveProjectId(projectId);
      setStep(3);
    } catch (e2) {
      const msg = e2?.response?.data?.detail || e2?.message || "Submit failed.";
      setErr(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      data-testid="new-user-wizard"
      role="dialog" aria-modal="true" aria-labelledby="wizard-title"
      style={{
        position: "fixed", inset: 0, zIndex: 9000,
        background: "rgba(8,10,14,0.72)",
        backdropFilter: "blur(8px)", WebkitBackdropFilter: "blur(8px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 16,
      }}
    >
      <div style={{
        width: "min(580px, 100%)",
        background: "var(--panel, #0f1219)",
        border: "1px solid var(--border, rgba(255,200,120,0.18))",
        borderRadius: 10,
        boxShadow: "0 24px 60px -16px rgba(255,138,42,0.18)",
        overflow: "hidden",
      }}>
        <header style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "12px 18px", borderBottom: "1px solid var(--border)",
        }}>
          <GitBranch size={14} style={{ color: "var(--accent, #ff8a2a)" }} />
          <div data-testid="wizard-progress" style={{
            fontSize: 11, fontWeight: 600, letterSpacing: "0.08em",
            textTransform: "uppercase", color: "var(--text-dim, #a39d8a)",
            flex: 1,
          }}>
            getting started · step {step} of 3
          </div>
          <button data-testid="wizard-close" onClick={close} title="Skip"
                  style={{ background:"transparent", border:"none", padding:4,
                           color:"var(--text-faint)", cursor:"pointer" }}>
            <X size={14} />
          </button>
        </header>

        {/* progress dots */}
        <div style={{ display:"flex", gap:6, padding:"10px 18px 0" }}>
          {[1,2,3].map((i) => (
            <div key={i} data-testid={`wizard-dot-${i}`} style={{
              width: i === step ? 22 : 6, height: 4, borderRadius: 2,
              background: i <= step ? "var(--accent, #ff8a2a)"
                                    : "var(--border, rgba(255,200,120,0.18))",
              transition: "width .2s ease, background .2s ease",
            }}/>
          ))}
        </div>

        <div style={{ padding: "16px 22px 4px" }}>
          {step === 1 && (
            <form onSubmit={submitRepo} data-testid="wizard-step-1">
              <h2 id="wizard-title" style={hStyle}>Connect your GitHub repo</h2>
              <p style={pStyle}>
                Paste a repo URL — ORA will read it, write the diff, and
                push the commit back to this branch.
              </p>
              <label style={lStyle}>Repository</label>
              <input
                data-testid="wizard-repo-input"
                autoFocus
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                placeholder="https://github.com/owner/repo"
                style={iStyle}
              />
              <label style={lStyle}>Branch</label>
              <input
                data-testid="wizard-branch-input"
                value={branch}
                onChange={(e) => setBranch(e.target.value)}
                placeholder="main"
                style={iStyle}
              />
              {err && <div data-testid="wizard-error" style={errStyle}>{err}</div>}
              <Footer
                busy={busy}
                primary="Continue"
                onPrimary={submitRepo}
                onSkip={close}
              />
            </form>
          )}

          {step === 2 && (
            <form onSubmit={submitTask} data-testid="wizard-step-2">
              <h2 style={hStyle}>What should ORA build first?</h2>
              <p style={pStyle}>{TASK_HINT}</p>
              <textarea
                data-testid="wizard-task-input"
                autoFocus
                value={task}
                onChange={(e) => setTask(e.target.value)}
                rows={5}
                placeholder="Add a /healthz endpoint that returns build hash + uptime…"
                style={{ ...iStyle, fontFamily:"var(--font-mono, ui-monospace, monospace)",
                         resize:"vertical", minHeight: 110 }}
              />
              {err && <div data-testid="wizard-error" style={errStyle}>{err}</div>}
              <Footer
                busy={busy}
                primary="Ship it"
                onPrimary={submitTask}
                onSkip={close}
              />
            </form>
          )}

          {step === 3 && (
            <div data-testid="wizard-step-3">
              <h2 style={hStyle}>ORA is shipping…</h2>
              <p style={pStyle}>
                Live progress below. You can leave this open or jump to
                the dashboard — the task keeps running in the background.
              </p>
              <TaskLiveTape taskId={taskId} />
              <div style={{ display:"flex", justifyContent:"flex-end",
                            gap: 8, padding: "14px 0 4px" }}>
                <button data-testid="wizard-goto-dashboard"
                        onClick={goDashboard} style={primaryBtn}>
                  Go to dashboard <ArrowRight size={12} />
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Footer({ busy, primary, onPrimary, onSkip }) {
  return (
    <div style={{ display:"flex", alignItems:"center",
                  gap: 8, padding: "16px 0 4px" }}>
      <button data-testid="wizard-skip-link" type="button" onClick={onSkip}
              style={{ background:"transparent", border:"none",
                       color:"var(--text-faint)", fontSize:11,
                       padding:"6px 4px", cursor:"pointer" }}>
        Skip for now
      </button>
      <div style={{ flex:1 }} />
      <button data-testid="wizard-next" type="button" onClick={onPrimary}
              disabled={busy} style={primaryBtn}>
        {busy
          ? <><Loader2 size={12} style={{ animation:"spin 1s linear infinite" }} /> working…</>
          : <>{primary} <ArrowRight size={12} /></>}
      </button>
    </div>
  );
}

const hStyle = { margin: 0, fontSize: 20, fontWeight: 500,
                 color: "var(--text)", letterSpacing: "-0.01em" };
const pStyle = { margin: "8px 0 14px", fontSize: 12.5, lineHeight: 1.55,
                 color: "var(--text-dim)" };
const lStyle = { display: "block", fontSize: 10, fontWeight: 600,
                 textTransform: "uppercase", letterSpacing: "0.08em",
                 color: "var(--text-faint)", margin: "10px 0 4px" };
const iStyle = { width: "100%", padding: "9px 12px", fontSize: 13,
                 background: "var(--bg-elev, #0a0c10)",
                 color: "var(--text)",
                 border: "1px solid var(--border, rgba(255,200,120,0.16))",
                 borderRadius: 5, outline: "none",
                 fontFamily: "var(--font-sans, system-ui)" };
const errStyle = { marginTop: 10, fontSize: 11, color: "var(--danger, #ff6b6b)",
                   background: "rgba(255,107,107,0.06)",
                   border: "1px solid rgba(255,107,107,0.2)",
                   padding: "8px 10px", borderRadius: 4 };
const primaryBtn = { display: "inline-flex", alignItems: "center", gap: 6,
                     padding: "8px 14px",
                     background: "var(--accent, #ff8a2a)",
                     color: "var(--bg, #0a0c10)", border: "none",
                     borderRadius: 4, fontSize: 12, fontWeight: 600,
                     letterSpacing: "0.04em", cursor: "pointer" };
