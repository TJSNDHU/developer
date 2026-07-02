/**
 * AddProjectWizard.jsx — Iter 212m-5 — 3-step "Add Project" flow.
 *
 *   Step 1  Repo identify  (free-form owner/repo or full URL)
 *   Step 2  Generate token + paste (auto-verify against THIS repo only)
 *   Step 3  Confirm summary + project name → save → land in chat
 *
 * No OAuth path. PAT-only. Per-repo fine-grained tokens are recommended
 * (Generate button deep-links to GitHub's fine-grained PAT page with
 * description pre-filled); classic PATs still accepted but surface an
 * over-scoped warning when they grant access to >1 repo.
 *
 * After successful save:
 *   - localStorage active project is set to the new project_id
 *   - parent's onAdded(projectId) is called (refreshes list / closes modal)
 *   - user is navigated to /dashboard so they can start chatting immediately
 */
import React, { useEffect, useState } from "react";
import {
  Github,
  ExternalLink,
  Check,
  AlertCircle,
  Loader2,
  ArrowRight,
  ArrowLeft,
  X as XIcon,
  ShieldCheck,
  ShieldAlert,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { toast } from "./Toast";
import { setActiveProjectId } from "./TabBar";

// ── Helpers ───────────────────────────────────────────────────────────

/** Parse a free-form `owner/repo` or `https://github.com/owner/repo` into
 *  canonical `{owner, repo, full_name, url}`. Returns null on invalid. */
export function parseRepoInput(raw) {
  const s = (raw || "")
    .trim()
    .replace(/^https?:\/\/github\.com\//i, "")
    .replace(/\.git$/i, "")
    .replace(/\/+$/, "");
  const parts = s.split("/").filter(Boolean);
  if (parts.length < 2) return null;
  return {
    owner:     parts[0],
    repo:      parts[1],
    full_name: `${parts[0]}/${parts[1]}`,
    url:       `https://github.com/${parts[0]}/${parts[1]}`,
  };
}

const PAT_RX = /^(ghp_|github_pat_)[A-Za-z0-9_]{20,}$/;

// ── Component ─────────────────────────────────────────────────────────

export default function AddProjectWizard({ onClose, onAdded }) {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);

  // Step-1 state
  const [repoInput, setRepoInput] = useState("");
  const repo = parseRepoInput(repoInput);

  // Step-2 state
  const [pat, setPat] = useState("");
  const [check, setCheck] = useState({ status: "idle" });
  // status: idle | loading | ok | error
  //   ok    → { full_name, scopes, total_accessible_repos, warning, fine_grained }
  //   error → { error, detail }

  // Step-3 state
  const [projectName, setProjectName] = useState("");
  const [saving, setSaving] = useState(false);

  // ── Auto-fill project name from repo name when entering step 3 ───
  useEffect(() => {
    if (step === 3 && !projectName.trim() && repo) {
      setProjectName(repo.repo);
    }
  }, [step, repo, projectName]);

  // ── Debounced PAT verification (step 2) ──────────────────────────
  useEffect(() => {
    if (step !== 2 || !pat.trim() || !repo) {
      setCheck({ status: "idle" });
      return;
    }
    if (!PAT_RX.test(pat.trim())) {
      setCheck({
        status: "error",
        error:  "bad_format",
        detail: "PAT should start with ghp_ or github_pat_ and be ≥ 20 chars.",
      });
      return;
    }
    const id = setTimeout(async () => {
      setCheck({ status: "loading" });
      try {
        const r = await api.post("/cto/projects/verify-pat", {
          repo: repo.full_name,
          pat:  pat.trim(),
        });
        const d = r.data || {};
        if (d.ok) {
          setCheck({ status: "ok", ...d });
        } else {
          setCheck({
            status: "error",
            error:  d.error || "unknown",
            detail: d.detail || "Verification failed.",
          });
        }
      } catch (e) {
        setCheck({
          status: "error",
          error:  "network_error",
          detail: e?.response?.data?.detail || "Couldn't reach verifier.",
        });
      }
    }, 700);
    return () => clearTimeout(id);
  }, [step, pat, repo]);

  // ── Step-3 save ──────────────────────────────────────────────────
  async function handleSave() {
    if (!repo || !pat.trim() || check.status !== "ok" || !projectName.trim()) {
      toast({ message: "Fill all fields and verify the PAT first.", kind: "warn" });
      return;
    }
    setSaving(true);
    try {
      const r = await api.post("/cto/projects/add", {
        name:         projectName.trim(),
        github_url:   repo.url,
        github_token: pat.trim(),
        branch:       "main",
      });
      const newProjectId = r.data?.project?.project_id || r.data?.project_id;
      toast({
        message: `Connected ${repo.full_name} — opening chat…`,
        kind:    "success",
      });
      if (newProjectId) {
        setActiveProjectId(newProjectId);
      }
      try { onAdded?.(newProjectId); } catch { /* noop */ }
      // Land directly in chat with this project active.
      navigate("/dashboard");
    } catch (e) {
      toast({
        message: e?.response?.data?.detail || "Connect failed",
        kind:    "error",
      });
    } finally {
      setSaving(false);
    }
  }

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div
      onClick={onClose}
      data-testid="add-project-wizard-backdrop"
      style={{
        position: "fixed", inset: 0, zIndex: 9000,
        background: "rgba(0,0,0,0.68)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        data-testid="add-project-wizard"
        style={{
          width: "100%", maxWidth: 580,
          background: "var(--panel)",
          border: "1px solid var(--border-strong)",
          borderRadius: 12,
          padding: 24,
        }}
      >
        {/* ── Header + step indicator ── */}
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          marginBottom: 18,
        }}>
          <div>
            <div style={{
              fontSize: 10, color: "var(--accent-2, #FF8A2A)",
              letterSpacing: "0.14em", textTransform: "uppercase",
              fontFamily: "'JetBrains Mono', monospace", marginBottom: 4,
            }}>
              Add Project · Step {step} of 3
            </div>
            <h3 style={{ margin: 0, fontSize: 19, color: "var(--text)" }}>
              {step === 1 && "Which GitHub repo?"}
              {step === 2 && "Generate & paste your token"}
              {step === 3 && "Confirm and start"}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            data-testid="add-project-close"
            aria-label="Close"
            style={{
              background: "none", border: "none",
              color: "var(--text-faint)", cursor: "pointer", padding: 4,
            }}
          >
            <XIcon size={16} />
          </button>
        </div>

        {/* ── Stepper dots ── */}
        <div style={{
          display: "flex", alignItems: "center", gap: 6, marginBottom: 22,
        }}>
          {[1, 2, 3].map((n) => (
            <div
              key={n}
              data-testid={`step-dot-${n}`}
              style={{
                height: 4, flex: 1, borderRadius: 2,
                background: n <= step
                  ? "var(--accent-2, #FF8A2A)"
                  : "rgba(255,255,255,0.08)",
                transition: "background 200ms",
              }}
            />
          ))}
        </div>

        {/* ──────────────── Step 1 — Repo input ──────────────── */}
        {step === 1 && (
          <div data-testid="wizard-step-1">
            <p style={{
              fontSize: 13, color: "var(--text-dim)",
              margin: "0 0 16px", lineHeight: 1.6,
            }}>
              Type the repo as <code style={codeChip}>owner/repo</code>{" "}
              or paste the full GitHub URL.
            </p>
            <input
              data-testid="repo-input"
              autoFocus
              type="text"
              spellCheck={false}
              autoComplete="off"
              value={repoInput}
              onChange={(e) => setRepoInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && repo) setStep(2);
              }}
              placeholder="e.g. facebook/react   •   or https://github.com/owner/repo"
              style={{
                width: "100%", padding: "14px 16px",
                background: "rgba(0,0,0,0.32)",
                color: "var(--text)",
                fontFamily: "'JetBrains Mono', monospace", fontSize: 14,
                border: `1.5px solid ${
                  repoInput && !repo ? "rgba(239,68,68,0.5)" :
                  repo               ? "rgba(34,197,94,0.5)"  :
                  "rgba(245,158,11,0.4)"
                }`,
                borderRadius: 8, outline: "none",
              }}
            />
            {repoInput && !repo && (
              <div style={{ fontSize: 11, color: "#ef4444", marginTop: 8 }}>
                Use the format <code style={codeChip}>owner/repo</code> (e.g.{" "}
                <code style={codeChip}>octocat/Hello-World</code>).
              </div>
            )}
            {repo && (
              <div
                data-testid="repo-parsed"
                style={{
                  marginTop: 10, fontSize: 12, color: "#22c55e",
                  fontFamily: "'JetBrains Mono', monospace",
                  display: "inline-flex", alignItems: "center", gap: 6,
                }}
              >
                <Check size={12} /> Repo set — github.com/{repo.full_name}
              </div>
            )}

            <div style={navRowStyle}>
              <button
                type="button"
                onClick={onClose}
                data-testid="wizard-cancel-1"
                className="btn-ghost"
                style={{ padding: "10px 18px", fontSize: 12 }}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => setStep(2)}
                disabled={!repo}
                data-testid="wizard-next-1"
                style={cta(!!repo)}
              >
                Next <ArrowRight size={14} />
              </button>
            </div>
          </div>
        )}

        {/* ──────────────── Step 2 — Generate + paste ──────────────── */}
        {step === 2 && repo && (
          <div data-testid="wizard-step-2">
            <p style={{
              fontSize: 13, color: "var(--text-dim)",
              margin: "0 0 14px", lineHeight: 1.6,
            }}>
              Generate a token <strong style={{ color: "var(--text)" }}>scoped
              just to this repo</strong>, then paste it below. We&apos;ll
              verify access before saving.
            </p>

            <a
              href={
                "https://github.com/settings/personal-access-tokens/new" +
                "?description=" + encodeURIComponent(`ORA · ${repo.repo}`) +
                "&expires_in=90&contents=write"
              }
              target="_blank"
              rel="noopener noreferrer"
              data-testid="generate-token-cta"
              style={{
                display: "flex", alignItems: "center", justifyContent: "center",
                gap: 10, padding: 13,
                background: "#24292e", color: "#fff",
                border: "2px solid var(--accent-2, #FF8A2A)",
                borderRadius: 10, textDecoration: "none",
                fontSize: 14, fontWeight: 500, marginBottom: 14,
              }}
            >
              <Github size={18} />
              Generate token for {repo.repo}
              <ExternalLink size={13} style={{ opacity: 0.75 }} />
            </a>

            <ol style={{
              margin: "0 0 14px", paddingLeft: 18,
              fontSize: 12, lineHeight: 1.7, color: "var(--text-dim)",
            }}>
              <li>
                <strong style={{ color: "var(--text)" }}>Repository access:</strong>{" "}
                pick <em>Only select repositories</em> →{" "}
                <code style={codeChip}>{repo.full_name}</code>
              </li>
              <li>
                <strong style={{ color: "var(--text)" }}>Permissions:</strong>{" "}
                under <em>Repository permissions</em> set{" "}
                <code style={codeChip}>Contents: Read and write</code>
              </li>
              <li>
                Click <em>Generate token</em>, copy it, paste below.
              </li>
            </ol>

            <label style={{ display: "grid", gap: 6, marginBottom: 4 }}>
              <span style={{
                fontSize: 11, color: "var(--text-dim)",
                fontFamily: "'JetBrains Mono', monospace",
                letterSpacing: "0.06em", textTransform: "uppercase",
              }}>
                Paste your PAT
              </span>
              <input
                data-testid="pat-input"
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={pat}
                onChange={(e) => setPat(e.target.value)}
                placeholder="github_pat_xxx or ghp_xxx"
                className="input"
                style={{
                  fontFamily: "'JetBrains Mono', monospace", fontSize: 12,
                  borderColor:
                    check.status === "ok"      ? "rgba(34,197,94,0.55)"  :
                    check.status === "error"   ? "rgba(239,68,68,0.55)"  :
                    check.status === "loading" ? "rgba(245,158,11,0.4)" :
                    undefined,
                }}
              />
            </label>

            {/* Verify pill */}
            {check.status === "loading" && (
              <div data-testid="pat-verify-loading" style={pillStyle("loading")}>
                <Loader2 size={11} className="animate-spin" /> Checking against {repo.full_name}…
              </div>
            )}
            {check.status === "ok" && (
              <div data-testid="pat-verify-ok" style={pillStyle("ok")}>
                <Check size={12} /> Token valid — access to {check.full_name}
              </div>
            )}
            {check.status === "error" && (
              <div data-testid="pat-verify-error" style={pillStyle("error")}>
                <AlertCircle size={12} style={{ marginTop: 1 }} />
                <span>{check.detail}</span>
              </div>
            )}

            <div style={navRowStyle}>
              <button
                type="button"
                onClick={() => setStep(1)}
                data-testid="wizard-back-2"
                className="btn-ghost"
                style={{ padding: "10px 18px", fontSize: 12 }}
              >
                <ArrowLeft size={12} /> Back
              </button>
              <button
                type="button"
                onClick={() => setStep(3)}
                disabled={check.status !== "ok"}
                data-testid="wizard-next-2"
                style={cta(check.status === "ok")}
              >
                Next <ArrowRight size={14} />
              </button>
            </div>
          </div>
        )}

        {/* ──────────────── Step 3 — Confirm + save ──────────────── */}
        {step === 3 && repo && check.status === "ok" && (
          <div data-testid="wizard-step-3">
            <p style={{
              fontSize: 13, color: "var(--text-dim)",
              margin: "0 0 14px", lineHeight: 1.6,
            }}>
              Everything checks out. Confirm the project name and we&apos;ll
              take you to the chat.
            </p>

            {/* Summary card */}
            <div
              data-testid="confirm-summary"
              style={{
                padding: 14, marginBottom: 14,
                background: "rgba(34,197,94,0.06)",
                border: "1px solid rgba(34,197,94,0.32)",
                borderRadius: 10,
                display: "flex", flexDirection: "column", gap: 8,
                fontSize: 12, color: "var(--text)",
                fontFamily: "'JetBrains Mono', monospace",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <ShieldCheck size={14} style={{ color: "#22c55e" }} />
                <span>Access to <strong>{check.full_name}</strong> verified</span>
              </div>
              <div style={{
                fontSize: 11, color: "var(--text-dim)", paddingLeft: 22,
              }}>
                {check.fine_grained
                  ? "Fine-grained PAT · scope limited to this repo."
                  : `Classic PAT · scopes: ${(check.scopes || []).join(", ") || "—"}`}
              </div>
              {typeof check.total_accessible_repos === "number" && (
                <div style={{
                  fontSize: 11, color: "var(--text-dim)", paddingLeft: 22,
                }}>
                  Token grants access to{" "}
                  <strong style={{
                    color: check.warning ? "#f59e0b" : "#22c55e",
                  }}>
                    {check.total_accessible_repos}{" "}
                    repo{check.total_accessible_repos === 1 ? "" : "s"}
                  </strong>{" "}
                  in your GitHub account.
                </div>
              )}
              {check.warning && (
                <div
                  data-testid="confirm-warning"
                  style={{
                    marginTop: 4, padding: "8px 12px",
                    background: "rgba(245,158,11,0.10)",
                    border: "1px solid rgba(245,158,11,0.35)",
                    borderRadius: 8,
                    fontSize: 11, color: "#f59e0b",
                    fontFamily: "'JetBrains Mono', monospace",
                    display: "flex", alignItems: "flex-start", gap: 8,
                    lineHeight: 1.55,
                  }}
                >
                  <ShieldAlert size={13} style={{ marginTop: 1, flexShrink: 0 }} />
                  <span>{check.warning}</span>
                </div>
              )}
            </div>

            <label style={{ display: "grid", gap: 6, marginBottom: 4 }}>
              <span style={{
                fontSize: 11, color: "var(--text-dim)",
                fontFamily: "'JetBrains Mono', monospace",
                letterSpacing: "0.06em", textTransform: "uppercase",
              }}>
                Project name
              </span>
              <input
                data-testid="project-name-input"
                type="text"
                value={projectName}
                onChange={(e) => setProjectName(e.target.value)}
                placeholder={repo.repo}
                className="input"
                style={{
                  fontFamily: "'JetBrains Mono', monospace", fontSize: 13,
                }}
              />
            </label>

            <div style={navRowStyle}>
              <button
                type="button"
                onClick={() => setStep(2)}
                data-testid="wizard-back-3"
                className="btn-ghost"
                disabled={saving}
                style={{ padding: "10px 18px", fontSize: 12 }}
              >
                <ArrowLeft size={12} /> Back
              </button>
              <button
                type="button"
                onClick={handleSave}
                disabled={saving || !projectName.trim()}
                data-testid="wizard-save"
                style={cta(!saving && !!projectName.trim())}
              >
                {saving ? <Loader2 size={14} className="animate-spin" /> : null}
                {saving ? "Saving…" : "Save & Open Chat"}
                {!saving && <ArrowRight size={14} />}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Style atoms ───────────────────────────────────────────────────────

const codeChip = {
  background: "rgba(255,138,42,0.10)",
  color: "var(--accent-2, #FF8A2A)",
  padding: "1px 6px",
  borderRadius: 4,
  fontFamily: "'JetBrains Mono', monospace",
  fontSize: 11,
};

const navRowStyle = {
  display: "flex", justifyContent: "space-between", alignItems: "center",
  gap: 10, paddingTop: 18, marginTop: 18,
  borderTop: "0.5px solid var(--border, rgba(255,255,255,0.08))",
};

function cta(enabled) {
  return {
    display: "inline-flex", alignItems: "center", gap: 8,
    padding: "10px 22px",
    background: enabled ? "var(--accent-2, #FF8A2A)" : "rgba(255,255,255,0.06)",
    color:      enabled ? "#0a0e1a"               : "var(--text-faint)",
    border: "none", borderRadius: 8,
    fontSize: 13, fontWeight: 600,
    fontFamily: "'JetBrains Mono', monospace",
    cursor: enabled ? "pointer" : "not-allowed",
  };
}

function pillStyle(kind) {
  if (kind === "loading") {
    return {
      marginTop: 8, fontSize: 11, color: "#94a3b8",
      display: "inline-flex", alignItems: "center", gap: 6,
      padding: "4px 10px",
      background: "rgba(255,255,255,0.04)",
      border: "0.5px solid rgba(255,255,255,0.12)",
      borderRadius: 999, alignSelf: "flex-start",
    };
  }
  if (kind === "ok") {
    return {
      marginTop: 8, fontSize: 11, color: "#22c55e",
      display: "inline-flex", alignItems: "center", gap: 6,
      padding: "4px 10px",
      background: "rgba(34,197,94,0.10)",
      border: "0.5px solid rgba(34,197,94,0.35)",
      borderRadius: 999, alignSelf: "flex-start",
      fontFamily: "'JetBrains Mono', monospace",
    };
  }
  return {
    marginTop: 8, fontSize: 11, color: "#ef4444",
    display: "inline-flex", alignItems: "flex-start", gap: 6,
    padding: "6px 10px",
    background: "rgba(239,68,68,0.10)",
    border: "0.5px solid rgba(239,68,68,0.35)",
    borderRadius: 8, alignSelf: "flex-start", maxWidth: "100%",
  };
}
