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
import React, { useEffect, useRef, useState } from "react";
import { Loader2, X, ArrowRight, Github } from "lucide-react";
import { api, getToken, API_BASE } from "../lib/api";
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

  // GitHub OAuth state — drives whether step 1 shows a Connect button
  // or the repo URL input + picker.
  const [ghStatus, setGhStatus] = useState("checking"); // "checking" | "connected" | "disconnected"
  const [ghLogin, setGhLogin]   = useState("");
  const [repos, setRepos]       = useState([]);
  const [reposBusy, setReposBusy] = useState(false);
  const pollRef = useRef(null);
  const popupRef = useRef(null);

  // Initial OAuth status check.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await api.get("/github/oauth/status");
        if (cancelled) return;
        if (r.data?.connected) {
          setGhStatus("connected");
          setGhLogin(r.data.login || "");
          fetchRepos();
        } else {
          setGhStatus("disconnected");
        }
      } catch {
        if (!cancelled) setGhStatus("disconnected");
      }
    })();
    return () => {
      cancelled = true;
      if (pollRef.current) clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function fetchRepos() {
    setReposBusy(true);
    try {
      const r = await api.get("/github/oauth/repos");
      setRepos(r.data?.repos || []);
    } catch {
      setRepos([]);
    } finally {
      setReposBusy(false);
    }
  }

  function connectGithub() {
    const token = getToken();
    if (!token) {
      setErr("Session expired — please log in again.");
      return;
    }
    // Open the OAuth flow in a popup. The backend's /connect handler
    // accepts the JWT via `?auth=` so cookieless browsers still work.
    const url = `${API_BASE}/github/oauth/connect?auth=${encodeURIComponent(token)}`;
    const w = 560, h = 720;
    const left = Math.max(0, window.screenX + (window.outerWidth  - w) / 2);
    const top  = Math.max(0, window.screenY + (window.outerHeight - h) / 2);
    popupRef.current = window.open(
      url, "aurem_github_oauth",
      `width=${w},height=${h},left=${left},top=${top}`,
    );
    // Poll status every 2 s until either: connected, popup closed by user,
    // or 90 s timeout.  This is more reliable than postMessage across
    // GitHub's domain handoff.
    if (pollRef.current) clearInterval(pollRef.current);
    const started = Date.now();
    pollRef.current = setInterval(async () => {
      try {
        const r = await api.get("/github/oauth/status");
        if (r.data?.connected) {
          clearInterval(pollRef.current); pollRef.current = null;
          try { popupRef.current?.close?.(); } catch { /* xorigin */ }
          setGhStatus("connected");
          setGhLogin(r.data.login || "");
          fetchRepos();
        }
      } catch { /* keep polling */ }
      if (popupRef.current?.closed) {
        // user closed the window without finishing
        if (ghStatus !== "connected") {
          clearInterval(pollRef.current); pollRef.current = null;
        }
      }
      if (Date.now() - started > 90_000) {
        clearInterval(pollRef.current); pollRef.current = null;
      }
    }, 2000);
  }

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
      // If the server replies "GitHub not connected" mid-flow (e.g. user
      // is in manual mode for a private repo), flip the panel back to
      // the OAuth-connect view instead of asking them to leave.
      if (/github not connected/i.test(msg)) {
        setGhStatus("disconnected");
        setErr("This repo needs GitHub access. Connect once below — your manual URL will stick.");
      } else {
        setErr(msg);
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

  const robotMsg = buildRobotMessage({ step, ghStatus, busy, err, repoUrl, task, taskId });
  const stepLabel = ["Connect repo", "First task", "Shipping"][step - 1];

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
      <style>{WIZARD_KEYFRAMES}</style>
      <div style={{
        width: "min(440px, 100%)",
        background: "#0f172a",
        border: "0.5px solid rgba(255,255,255,0.1)",
        borderRadius: 14,
        boxShadow: "0 24px 60px -16px rgba(245,158,11,0.18)",
        overflow: "hidden",
      }}>
        {/* ORA brand header */}
        <header style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "14px 20px",
          borderBottom: "0.5px solid rgba(255,255,255,0.08)",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{
              width: 24, height: 24, background: "#f59e0b",
              borderRadius: "50%", display: "flex", alignItems: "center",
              justifyContent: "center", fontSize: 11, fontWeight: 700,
              color: "#000", fontFamily: "var(--font-mono, ui-monospace, monospace)",
            }}>O</div>
            <div>
              <div style={{ fontSize: 13, fontWeight: 500, color: "#f8fafc",
                            fontFamily: "var(--font-mono, ui-monospace, monospace)" }}>
                ORA
              </div>
              <div style={{ fontSize: 10, color: "#64748b" }}>by Aurem CTO</div>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div data-testid="wizard-progress" style={{
              fontSize: 11, color: "#64748b",
              fontFamily: "var(--font-mono, ui-monospace, monospace)",
            }}>
              Step {step} of 3
            </div>
            <button data-testid="wizard-close" onClick={close} title="Skip"
                    style={{ background:"transparent", border:"none", padding:4,
                             color:"#64748b", cursor:"pointer", display:"flex" }}>
              <X size={14} />
            </button>
          </div>
        </header>

        <div style={{ padding: "20px 20px 16px" }}>
          {/* Step dots + label */}
          <div style={{
            display: "flex", alignItems: "center", gap: 6, marginBottom: 18,
          }}>
            {[1,2,3].map((i) => (
              <div key={i} data-testid={`wizard-dot-${i}`} style={{
                width: i === step ? 20 : 8,
                height: 8,
                borderRadius: i === step ? 4 : "50%",
                background: i < step ? "#22c55e"
                          : i === step ? "#f59e0b"
                          : "rgba(255,255,255,0.15)",
                transition: "all .3s ease",
              }}/>
            ))}
            <div style={{
              fontSize: 11, color: "#64748b", marginLeft: 4,
              fontFamily: "var(--font-mono, ui-monospace, monospace)",
            }}>{stepLabel}</div>
          </div>

          {/* Robot Guide */}
          <RobotGuide message={robotMsg} kind={err ? "error" : "info"} />
          {step === 1 && (
            <form onSubmit={submitRepo} data-testid="wizard-step-1">
              <h2 id="wizard-title" style={hStyle}>Connect your GitHub repo</h2>

              {ghStatus === "checking" && (
                <div data-testid="wizard-gh-checking" style={{
                  display:"flex", alignItems:"center", gap:8,
                  fontSize:12, color:"var(--text-dim)",
                  padding:"18px 0",
                }}>
                  <Loader2 size={12} style={{ animation:"spin 1s linear infinite" }} />
                  Checking GitHub connection…
                </div>
              )}

              {ghStatus === "disconnected" && (
                <div data-testid="wizard-gh-disconnected">
                  <p style={pStyle}>
                    Connect GitHub once — AUREM will use it to read your
                    repos, write commits, and open PRs. We never store the
                    token in plaintext.
                  </p>
                  <div style={{ position: "relative" }}>
                    <div data-testid="wizard-pulse-ring" style={pulseRingStyle} />
                    <button
                      data-testid="wizard-connect-github"
                      type="button"
                      onClick={connectGithub}
                      style={githubBtnStyle}
                    >
                      <Github size={16} />
                      Continue with GitHub
                    </button>
                  </div>
                  <div style={{
                    fontSize: 10.5, color: "var(--text-faint)",
                    textAlign: "center", marginTop: 10, lineHeight: 1.5,
                  }}>
                    Opens in a popup. After authorising, this wizard will
                    pick up automatically.
                  </div>
                  {err && <div data-testid="wizard-error" style={errStyle}>{err}</div>}
                  <Footer
                    busy={false}
                    primary="Skip — paste a URL"
                    onPrimary={() => setGhStatus("manual")}
                    onSkip={close}
                  />
                </div>
              )}

              {(ghStatus === "connected" || ghStatus === "manual") && (
                <>
                  {ghStatus === "connected" && (
                    <div data-testid="wizard-gh-connected" style={{
                      display:"flex", alignItems:"center", gap:8,
                      padding:"6px 10px", marginBottom:12,
                      background:"rgba(109,212,161,0.07)",
                      border:"1px solid rgba(109,212,161,0.22)",
                      borderRadius:4, fontSize:11,
                      color:"var(--ok, #6dd4a1)",
                    }}>
                      <Github size={11} />
                      Connected as <strong>{ghLogin || "github user"}</strong>
                    </div>
                  )}
                  <p style={pStyle}>
                    {ghStatus === "connected"
                      ? "Pick a repo from your account or paste any URL — ORA will read it, write the diff, and push the commit back."
                      : "Paste any public repo URL. (You can connect GitHub later from Settings for private repos.)"}
                  </p>

                  {ghStatus === "connected" && (
                    <>
                      <label style={lStyle}>Your repositories</label>
                      <select
                        data-testid="wizard-repo-picker"
                        disabled={reposBusy}
                        onChange={(e) => {
                          const idx = parseInt(e.target.value, 10);
                          const r = repos[idx];
                          if (r) {
                            setRepoUrl(r.url || `https://github.com/${r.full_name}`);
                            setBranch(r.default_branch || "main");
                          }
                        }}
                        style={iStyle}
                        defaultValue=""
                      >
                        <option value="" disabled>
                          {reposBusy ? "Loading…" : `${repos.length} repos found — pick one`}
                        </option>
                        {repos.map((r, i) => (
                          <option key={r.full_name} value={i}>
                            {r.full_name}{r.private ? " · private" : ""}
                          </option>
                        ))}
                      </select>
                    </>
                  )}

                  <label style={lStyle}>Repository URL</label>
                  <input
                    data-testid="wizard-repo-input"
                    autoFocus={ghStatus !== "connected"}
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
                </>
              )}
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
                     padding: "10px 16px",
                     background: "#f59e0b",
                     color: "#0a0c10", border: "none",
                     borderRadius: 8, fontSize: 13, fontWeight: 600,
                     letterSpacing: "0.02em", cursor: "pointer" };

// ─────────────── Robot Guide subcomponent ───────────────
function RobotGuide({ message, kind = "info" }) {
  const isErr = kind === "error";
  return (
    <div data-testid="wizard-robot-guide" style={{
      background: isErr ? "rgba(255,107,107,0.06)" : "rgba(245,158,11,0.06)",
      border: isErr ? "1px solid rgba(255,107,107,0.3)"
                    : "1px solid rgba(245,158,11,0.25)",
      borderRadius: 12, padding: "12px 14px", marginBottom: 16,
      display: "flex", gap: 12, alignItems: "flex-start",
      transition: "all .3s ease",
    }}>
      <div data-testid="wizard-robot-face" style={{
        width: 36, height: 36,
        background: isErr ? "#ef4444" : "#f59e0b",
        borderRadius: 8, position: "relative", flexShrink: 0,
      }}>
        {/* eyes */}
        <div style={{ position:"absolute", top:9, left:7, width:7, height:7,
                       background:"#000", borderRadius:"50%",
                       animation:"oraBlink 3s infinite" }} />
        <div style={{ position:"absolute", top:9, right:7, width:7, height:7,
                       background:"#000", borderRadius:"50%",
                       animation:"oraBlink 3s infinite 0.1s" }} />
        {/* mouth */}
        <div style={{ position:"absolute", bottom:7, left:"50%",
                       transform:"translateX(-50%)", width:14, height:4,
                       background:"#000", borderRadius:2 }} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 10, color: isErr ? "#ef4444" : "#f59e0b",
          fontFamily: "var(--font-mono, ui-monospace, monospace)",
          letterSpacing: "0.08em", marginBottom: 4,
        }}>
          {isErr ? "ORA · HEADS UP" : "ORA GUIDE"}
        </div>
        <div data-testid="wizard-robot-msg"
             style={{ fontSize: 13, color: "#f8fafc", lineHeight: 1.55 }}
             dangerouslySetInnerHTML={{ __html: message }} />
      </div>
    </div>
  );
}

function buildRobotMessage({ step, ghStatus, busy, err, repoUrl, task, taskId }) {
  if (err) return `Hmm — <strong>${escapeHtml(err)}</strong>. Try again, or skip for now.`;
  if (busy) return `Working on it… <span class="ora-arrow">⏳</span>`;
  if (step === 1) {
    if (ghStatus === "checking") return `Checking your GitHub connection…`;
    if (ghStatus === "disconnected")
      return `<strong>Fastest way:</strong> click <strong>Continue with GitHub</strong> below <span class="ora-arrow">👇</span> — connects in seconds, no PAT needed.`;
    if (ghStatus === "manual")
      return `Paste any <strong>public repo URL</strong> below. For private repos, connect GitHub from Settings later. <span class="ora-arrow">👇</span>`;
    // connected
    if (!repoUrl) return `Your GitHub repos are loaded! <strong>Pick a repo</strong> from the dropdown — or paste a URL. <span class="ora-arrow">👇</span>`;
    return `Nice — <strong>${escapeHtml(repoUrl.replace(/^https?:\/\/github\.com\//, ""))}</strong> looks good. Click <strong>Continue</strong> to connect it. <span class="ora-arrow">👇</span>`;
  }
  if (step === 2) {
    if (!task) return `Tell me <strong>what to build first</strong>. Be specific — file paths help me ship faster. <span class="ora-arrow">👇</span>`;
    if (task.length < 12) return `A little more detail, please — <strong>12+ characters</strong> so I can scope it right.`;
    return `Looks shippable. Hit <strong>Ship it</strong> when you&rsquo;re ready. <span class="ora-arrow">👇</span>`;
  }
  if (step === 3) {
    if (!taskId) return `Spinning up the worker…`;
    return `<strong>Shipping live below.</strong> You can close this — the task keeps running in the background. <span class="ora-arrow">🚀</span>`;
  }
  return "";
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

const githubBtnStyle = {
  width: "100%", padding: "13px", background: "#24292e",
  color: "#fff", border: "2px solid #f59e0b", borderRadius: 10,
  fontSize: 14, fontWeight: 500, cursor: "pointer",
  display: "flex", alignItems: "center", justifyContent: "center",
  gap: 10, marginBottom: 4, transition: "all .2s",
  position: "relative", zIndex: 1,
};

const pulseRingStyle = {
  position: "absolute", inset: -4, borderRadius: 12,
  border: "2px solid #f59e0b", pointerEvents: "none",
  animation: "oraPulseRing 1.5s infinite",
};

const WIZARD_KEYFRAMES = `
@keyframes oraBlink {
  0%,90%,100% { transform: scaleY(1); }
  95% { transform: scaleY(0.1); }
}
@keyframes oraPulseRing {
  0% { opacity: 1; transform: scale(1); }
  100% { opacity: 0; transform: scale(1.08); }
}
@keyframes oraBounce {
  0%,100% { transform: translateY(0); }
  50% { transform: translateY(-3px); }
}
.ora-arrow { display: inline-block; animation: oraBounce 1s infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
`;
