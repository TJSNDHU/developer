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
import RobotGuide, { RobotGuideKeyframes, escapeHtml, oraPulseRingStyle } from "./RobotGuide";

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
  // Iter 212m-92 — per-project PAT input. Backend rejects /projects/add
  // without a github_token even when GitHub OAuth is connected (OAuth
  // is identity-only since iter 211). The wizard now collects this
  // directly so users don't hit the dead-end error state in
  // production where the helper link wasn't clickable.
  const [pat, setPat] = useState("");
  // Iter 212m-94 — track "user clicked Generate PAT" so we can:
  //   • auto-focus the PAT input when they tab back from GitHub
  //   • show a glowing visual cue + success step indicator
  //   • dismiss the cue once they've actually pasted something
  const [patGenClicked, setPatGenClicked] = useState(false);
  const patInputRef = React.useRef(null);
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

  // Iter 212m-94 — auto-focus the PAT input when the user tabs back
  // from the GitHub PAT-creation page. We listen for window focus
  // events and snap focus + scroll to the PAT input if the user has
  // clicked "Generate PAT" but hasn't yet pasted a token.
  useEffect(() => {
    if (!patGenClicked || pat) return;
    const onFocus = () => {
      // small delay so the focus lands AFTER browser tab switch animation
      setTimeout(() => {
        if (patInputRef.current) {
          patInputRef.current.focus();
          patInputRef.current.scrollIntoView({
            behavior: "smooth", block: "center",
          });
        }
      }, 250);
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [patGenClicked, pat]);

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
        github_token: pat.trim() || undefined,
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
      <RobotGuideKeyframes />
      <div style={{
        width: "min(460px, 100%)",
        maxHeight: "92vh",
        background: "#0f172a",
        border: "0.5px solid rgba(255,255,255,0.1)",
        borderRadius: 14,
        boxShadow: "0 24px 60px -16px rgba(245,158,11,0.18)",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
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

        <div style={{ padding: "20px 20px 16px", overflowY: "auto",
                       flex: "1 1 auto", minHeight: 0 }}>
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
          <RobotGuide message={robotMsg} kind={err ? "error" : "info"} testid="wizard-robot-guide" />
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
                    <div data-testid="wizard-pulse-ring" style={oraPulseRingStyle} />
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

                  {/* Iter 212m-92 — PAT input + Generate PAT CTA.
                      Without this users hit a dead-end "Personal Access
                      Token required" error with no actionable button. */}
                  <label style={lStyle} htmlFor="wizard-pat-input">
                    GitHub Personal Access Token
                    <span style={{ color: "var(--text-faint)",
                                   marginLeft: 6, fontSize: 10 }}>
                      (required · Contents: Read &amp; write)
                    </span>
                  </label>
                  <div style={{
                    display: "flex", gap: 6, alignItems: "stretch",
                    transition: "all .25s ease",
                    ...(patGenClicked && !pat ? {
                      // Glow ring when user just generated PAT but hasn't pasted yet
                      boxShadow: "0 0 0 3px rgba(255,102,8,0.28)",
                      borderRadius: 6,
                    } : {}),
                  }}>
                    <input
                      id="wizard-pat-input"
                      data-testid="wizard-pat-input"
                      ref={patInputRef}
                      type="password"
                      autoComplete="off"
                      value={pat}
                      onChange={(e) => setPat(e.target.value)}
                      placeholder={patGenClicked && !pat
                        ? "Paste your fresh PAT here ↓"
                        : "ghp_… or github_pat_…"}
                      style={{ ...iStyle, flex: 1, fontFamily:
                        "var(--font-mono, ui-monospace, monospace)",
                        ...(patGenClicked && !pat
                          ? { borderColor: "#FF6608" } : {}),
                      }}
                    />
                    <a
                      data-testid="wizard-generate-pat-btn"
                      href="https://github.com/settings/tokens/new?scopes=repo,workflow,read:user,user:email&description=AUREM%20CTO%20(per-project)&default_expires_at=90"
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={() => setPatGenClicked(true)}
                      style={{
                        display: "inline-flex", alignItems: "center",
                        gap: 6, padding: "9px 14px", fontSize: 12,
                        fontWeight: 600, whiteSpace: "nowrap",
                        background: "#FF6608", color: "#0A0A0A",
                        border: "1px solid #FF6608", borderRadius: 6,
                        textDecoration: "none", cursor: "pointer",
                      }}
                    >{patGenClicked ? "Open GitHub again" : "Generate PAT →"}</a>
                  </div>

                  {/* Iter 212m-94 — Strong "next step" CTA visible only
                      after Generate PAT click. Walks the user back
                      from GitHub to the paste-here input. */}
                  {patGenClicked && !pat && (
                    <div data-testid="wizard-paste-pat-cta" style={{
                      marginTop: 8, padding: "10px 12px", borderRadius: 6,
                      background: "rgba(255,102,8,0.08)",
                      border: "1px solid rgba(255,102,8,0.36)",
                      color: "#FF6608", fontSize: 12, fontWeight: 600,
                      display: "flex", alignItems: "center", gap: 8,
                      fontFamily: "var(--font-mono, ui-monospace, monospace)",
                    }}>
                      <span style={{
                        display: "inline-block", width: 6, height: 6,
                        borderRadius: "50%", background: "#FF6608",
                        boxShadow: "0 0 8px #FF6608",
                        animation: "oraBlink 1.5s infinite",
                      }} />
                      <span style={{ flex: 1 }}>
                        GitHub tab opened ↑ — copy your new token, paste it above, then hit <strong>Continue</strong>.
                      </span>
                    </div>
                  )}

                  {pat && pat.length > 10 && (
                    <div data-testid="wizard-pat-ready" style={{
                      marginTop: 8, padding: "8px 12px", borderRadius: 6,
                      background: "rgba(34,197,94,0.08)",
                      border: "1px solid rgba(34,197,94,0.36)",
                      color: "#22C55E", fontSize: 11, fontWeight: 600,
                      fontFamily: "var(--font-mono, ui-monospace, monospace)",
                    }}>
                      ✓ Token detected — click <strong>Continue</strong> to connect this repo.
                    </div>
                  )}
                  <p style={{
                    margin: "6px 0 0", fontSize: 10,
                    color: "var(--text-faint)",
                    fontFamily: "var(--font-mono, ui-monospace, monospace)",
                  }}>
                    Encrypted at rest · only used to read &amp; push this repo
                  </p>

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

function buildRobotMessage({ step, ghStatus, busy, err, repoUrl, task, taskId }) {
  if (err) {
    // Iter 212m-92 — if the error mentions PAT, inject a clickable
    // "Generate PAT" link so the user has a one-tap path forward
    // (used to be plain text URL — production users got stuck).
    const isPatErr = /personal access token|github_pat_|ghp_/i.test(err);
    if (isPatErr) {
      return `Hmm — <strong>GitHub Personal Access Token needed.</strong> ` +
        `Tap <a href="https://github.com/settings/tokens/new?scopes=repo&description=AUREM%20CTO" target="_blank" rel="noopener" style="color:#FF6608;text-decoration:underline;font-weight:600;">Generate PAT on GitHub →</a> ` +
        `then paste it in the field below. Or skip for now.`;
    }
    return `Hmm — <strong>${escapeHtml(err)}</strong>. Try again, or skip for now.`;
  }
  if (busy) return `Working on it… <span class="ora-arrow">⏳</span>`;
  if (step === 1) {
    if (ghStatus === "checking") return `Checking your GitHub connection…`;
    if (ghStatus === "disconnected")
      return `<strong>Fastest way:</strong> click <strong>Continue with GitHub</strong> below <span class="ora-arrow">👇</span> — connects in seconds, no PAT needed.`;
    if (ghStatus === "manual")
      return `Paste any <strong>public repo URL</strong> below. For private repos, connect GitHub from Settings later. <span class="ora-arrow">👇</span>`;
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

const githubBtnStyle = {
  width: "100%", padding: "13px", background: "#24292e",
  color: "#fff", border: "2px solid #f59e0b", borderRadius: 10,
  fontSize: 14, fontWeight: 500, cursor: "pointer",
  display: "flex", alignItems: "center", justifyContent: "center",
  gap: 10, marginBottom: 4, transition: "all .2s",
  position: "relative", zIndex: 1,
};
