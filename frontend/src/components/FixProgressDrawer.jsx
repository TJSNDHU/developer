/**
 * FixProgressDrawer.jsx — Iter 212m-148
 *
 * Slide-up drawer over the PersistentFixBar that renders the live
 * progress of the Bulk Fix job.  All state is read from the global
 * FixJobContext — the drawer NEVER owns the SSE connection.
 *
 * Critical contract:
 *   - Backdrop click / Escape → hidePanel() (UI only; does NOT cancel
 *     the job).
 *   - Component is ALWAYS mounted; visibility driven by CSS transform.
 *   - SSE stays alive even when this component is invisible.
 *   - Dismiss only available from inside the bar, in terminal states.
 *
 * Visuals preserved from Iter 212m-147:
 *   - Animated diff block with 40 ms stagger
 *   - Active Fix Card → Completed Fixes List → Final Summary
 *   - Heartbeat pulse + running clock + event counter + restart
 */
import React, { useEffect, useRef, useState } from "react";
import {
  X, GitCommit, GitPullRequest, ShieldCheck, ShieldAlert, ExternalLink,
  Loader2, FileSearch, Sparkles, UploadCloud, BadgeCheck, RotateCw,
  ChevronDown, ChevronUp, CheckCircle2, AlertCircle,
} from "lucide-react";
import { useFixJob } from "./FixJobContext";

const PHASE_META = {
  queued:           { Icon: FileSearch,  color: "#94a3b8", label: "Queued" },
  reading:          { Icon: FileSearch,  color: "#7dd3fc", label: "Reading file" },
  generating:       { Icon: Sparkles,    color: "#a855f7", label: "AI generating patch" },
  "fix-diff":       { Icon: Sparkles,    color: "#c084fc", label: "Diff ready" },
  "fix-committing": { Icon: UploadCloud, color: "#fbbf24", label: "Committing to GitHub" },
  committing:       { Icon: UploadCloud, color: "#fbbf24", label: "Committing" },
  verifying:        { Icon: BadgeCheck,  color: "#38bdf8", label: "Verifying commit" },
};

function shortSha(s) { return (s || "").slice(0, 7); }
function basename(p) {
  if (!p) return "";
  const i = p.lastIndexOf("/");
  return i >= 0 ? p.slice(i + 1) : p;
}

/* ── Animated diff block ──────────────────────────────────────────── */
function DiffBlock({ diff }) {
  if (!Array.isArray(diff) || diff.length === 0) return null;
  return (
    <div
      data-testid="fix-diff-block"
      style={{
        marginTop: 10,
        background: "#06080d",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: 6,
        overflow: "hidden",
        maxHeight: 360,
        overflowY: "auto",
      }}>
      <div style={{
        padding: "6px 10px",
        background: "rgba(255,255,255,0.02)",
        borderBottom: "1px solid rgba(255,255,255,0.05)",
        fontSize: 10, color: "#64748b",
        fontFamily: "'JetBrains Mono', monospace",
        letterSpacing: "0.04em", textTransform: "uppercase",
      }}>
        Diff · {diff.filter((d) => d.type === "add").length} added · {diff.filter((d) => d.type === "remove").length} removed
      </div>
      <pre style={{
        margin: 0, padding: "8px 0",
        fontSize: 11, lineHeight: 1.55,
        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        whiteSpace: "pre", overflow: "auto",
      }}>
        {diff.map((d, idx) => {
          const isAdd     = d.type === "add";
          const isRemove  = d.type === "remove";
          const isHunk    = d.type === "hunk";
          let prefix = "  ", color = "#94a3b8", bg = "transparent";
          if (isAdd)    { prefix = "+ "; color = "#86efac"; bg = "rgba(34,197,94,0.07)"; }
          if (isRemove) { prefix = "- "; color = "#fca5a5"; bg = "rgba(239,68,68,0.07)"; }
          if (isHunk)   { prefix = "  "; color = "#7dd3fc"; bg = "rgba(56,189,248,0.05)"; }
          return (
            <div key={idx}
                 data-testid={`fix-diff-line-${idx}`} data-type={d.type}
                 style={{
                   display: "grid", gridTemplateColumns: "20px 1fr",
                   padding: "0 10px", background: bg, color,
                   opacity: 0, transform: "translateY(4px)",
                   animation: "diffLineIn 240ms ease-out forwards",
                   animationDelay: `${Math.min(idx, 40) * 40}ms`,
                 }}>
              <span style={{ color: isAdd ? "#4ade80" : isRemove ? "#f87171" : "#475569", userSelect: "none" }}>{prefix}</span>
              <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{d.line || " "}</span>
            </div>
          );
        })}
      </pre>
    </div>
  );
}

/* ── Active fix card ──────────────────────────────────────────────── */
function ActiveFixCard({ row, fixIndex, fixTotal }) {
  if (!row) return null;
  const phase = row.phase || "queued";
  const meta  = PHASE_META[phase] || PHASE_META.queued;
  const Icon  = meta.Icon;
  const stageBadge = (() => {
    if (phase === "fix-committing" || phase === "committing")
      return { label: "COMMITTING", bg: "rgba(251,191,36,0.12)", border: "rgba(251,191,36,0.40)", color: "#fde68a" };
    if (phase === "verifying")
      return { label: "VERIFYING",  bg: "rgba(56,189,248,0.12)", border: "rgba(56,189,248,0.40)", color: "#7dd3fc" };
    if (phase === "fix-diff")
      return { label: "PATCH READY", bg: "rgba(192,132,252,0.12)", border: "rgba(192,132,252,0.40)", color: "#d8b4fe" };
    if (phase === "generating")
      return { label: "GENERATING", bg: "rgba(168,85,247,0.12)", border: "rgba(168,85,247,0.40)", color: "#d8b4fe" };
    if (phase === "reading")
      return { label: "READING",    bg: "rgba(125,211,252,0.12)", border: "rgba(125,211,252,0.40)", color: "#bae6fd" };
    if (phase === "retrying")
      return { label: `RETRY ${row.attempt}/${row.attempts_of || 3}`, bg: "rgba(250,204,21,0.12)", border: "rgba(250,204,21,0.40)", color: "#fde68a" };
    return { label: "QUEUED", bg: "rgba(148,163,184,0.12)", border: "rgba(148,163,184,0.30)", color: "#cbd5e1" };
  })();

  return (
    <div
      data-testid="fix-active-card"
      style={{
        marginBottom: 12, padding: 14, borderRadius: 10,
        background: "linear-gradient(180deg, rgba(251,146,60,0.06), rgba(251,146,60,0.015))",
        border: "1px solid rgba(251,146,60,0.30)",
        boxShadow: "0 0 24px -8px rgba(251,146,60,0.25)",
      }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <Icon size={14} color={meta.color} className={phase !== "queued" ? "anim-spin" : ""} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
            fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
          }}>
            <span style={{
              padding: "2px 8px", borderRadius: 999,
              background: stageBadge.bg,
              border: `1px solid ${stageBadge.border}`,
              color: stageBadge.color,
              fontWeight: 700, fontSize: 9, letterSpacing: "0.06em",
            }}>{stageBadge.label}</span>
            <span style={{
              padding: "2px 8px", borderRadius: 999,
              background: "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,255,255,0.10)",
              color: "#cbd5e1", fontWeight: 600, fontSize: 9,
            }}>FIX {fixIndex}/{fixTotal}</span>
            <code style={{ color: "#fdba74" }}>{row.rule_id || "unknown"}</code>
          </div>
          <div style={{
            marginTop: 4, fontSize: 11, color: "#cbd5e1",
            fontFamily: "'JetBrains Mono', monospace",
            wordBreak: "break-all",
          }}>{row.file || "—"}</div>
        </div>
      </div>
      {row.diff && row.diff.length > 0 && <DiffBlock diff={row.diff} />}
      {(phase === "fix-committing" || phase === "committing" || phase === "verifying") && (
        <div style={{
          marginTop: 10, padding: 8, borderRadius: 6,
          background: "rgba(251,191,36,0.05)",
          border: "1px solid rgba(251,191,36,0.20)",
          display: "flex", alignItems: "center", gap: 8,
          fontSize: 11, color: "#fde68a",
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          <Loader2 size={12} className="anim-spin" />
          {phase === "verifying"
            ? "Verifying commit lands on GitHub…"
            : "Pushing commit to GitHub…"}
          {row.commit_sha && (
            <code style={{ marginLeft: "auto", color: "#7dd3fc" }}>
              {shortSha(row.commit_sha)}
            </code>
          )}
        </div>
      )}
      {row.last_error && phase === "retrying" && (
        <div
          data-testid={`fix-active-retry-${row.finding_id}`}
          style={{
            marginTop: 10, padding: 8, borderRadius: 6,
            background: "rgba(250,204,21,0.06)",
            border: "1px solid rgba(250,204,21,0.25)",
            color: "#fde68a", fontSize: 11,
            fontFamily: "'JetBrains Mono', monospace",
            wordBreak: "break-word",
          }}
          title={row.last_error}>
          ⚠ {row.last_error.slice(0, 240)}
        </div>
      )}
    </div>
  );
}

/* ── Completed fix row ──────────────────────────────────────────────── */
function CompletedRow({ row }) {
  const [expanded, setExpanded] = useState(false);
  const isFailed = row.ok === false;
  return (
    <div
      data-testid={`fix-row-${row.finding_id}`}
      style={{
        padding: 10, marginBottom: 6, borderRadius: 8,
        background: isFailed ? "rgba(239,68,68,0.05)" : "rgba(34,197,94,0.04)",
        border: `1px solid ${isFailed ? "rgba(239,68,68,0.22)" : "rgba(34,197,94,0.22)"}`,
      }}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        style={{
          width: "100%", background: "transparent", border: 0, padding: 0,
          cursor: "pointer", color: "inherit", textAlign: "left",
          display: "flex", alignItems: "center", gap: 8,
          fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
        }}>
        {isFailed
          ? <ShieldAlert size={12} color="#fca5a5" />
          : <ShieldCheck size={12} color="#86efac" />}
        <span style={{ color: isFailed ? "#fca5a5" : "#86efac", fontWeight: 700, fontSize: 10, letterSpacing: "0.04em" }}>
          {isFailed ? "FAILED" : "FIXED"}
        </span>
        <code style={{ color: "#cbd5e1" }}>{row.rule_id || "unknown"}</code>
        <span style={{ marginLeft: "auto", color: "#64748b", fontSize: 10 }}>
          {basename(row.file)}
        </span>
        {expanded ? <ChevronUp size={11} color="#64748b" /> : <ChevronDown size={11} color="#64748b" />}
      </button>
      <div style={{
        marginTop: 6, fontSize: 10, color: "#94a3b8",
        fontFamily: "'JetBrains Mono', monospace", wordBreak: "break-all",
      }}>{row.file}</div>
      {!isFailed && (
        <div style={{
          marginTop: 6, display: "flex", alignItems: "center",
          gap: 6, flexWrap: "wrap", fontSize: 11,
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          {row.commit_sha && (
            <a
              data-testid={`fix-row-commit-${row.finding_id}`}
              href={row.html_url} target="_blank" rel="noopener noreferrer"
              style={{
                display: "inline-flex", alignItems: "center", gap: 4,
                padding: "2px 6px", borderRadius: 999,
                background: "rgba(56,189,248,0.10)",
                border: "1px solid rgba(56,189,248,0.35)",
                color: "#7dd3fc", textDecoration: "none",
              }}>
              <GitCommit size={10} />
              {shortSha(row.commit_sha)}
              <ExternalLink size={10} />
            </a>
          )}
          {row.pr_url && (
            <a href={row.pr_url} target="_blank" rel="noopener noreferrer"
               style={{
                 display: "inline-flex", alignItems: "center", gap: 4,
                 padding: "2px 6px", borderRadius: 999,
                 background: "rgba(168,85,247,0.10)",
                 border: "1px solid rgba(168,85,247,0.35)",
                 color: "#d8b4fe", textDecoration: "none",
               }}>
              <GitPullRequest size={10} />
              Draft PR
            </a>
          )}
          {row.verified && (
            <span style={{
              padding: "2px 6px", borderRadius: 999,
              background: "rgba(34,197,94,0.10)",
              border: "1px solid rgba(34,197,94,0.30)",
              color: "#86efac",
            }}>GitHub verified ✓</span>
          )}
        </div>
      )}
      {isFailed && row.error && (
        <div style={{
          marginTop: 6, fontSize: 11, color: "#fca5a5",
          fontFamily: "'JetBrains Mono', monospace",
          wordBreak: "break-word",
        }}>{row.error}</div>
      )}
      {expanded && row.diff && row.diff.length > 0 && (
        <DiffBlock diff={row.diff} />
      )}
    </div>
  );
}

/* ── Final summary ──────────────────────────────────────────────── */
function FinalSummaryCard({ terminal, completed, failed, total, durationStr, jobId }) {
  const allOk = failed === 0 && completed > 0;
  return (
    <div
      data-testid="fix-final-summary"
      style={{
        marginBottom: 12, padding: 18, borderRadius: 12,
        background: allOk
          ? "linear-gradient(180deg, rgba(34,197,94,0.10), rgba(34,197,94,0.02))"
          : (failed > 0
            ? "linear-gradient(180deg, rgba(239,68,68,0.10), rgba(239,68,68,0.02))"
            : "linear-gradient(180deg, rgba(148,163,184,0.08), rgba(148,163,184,0.02))"),
        border: `1px solid ${allOk
          ? "rgba(34,197,94,0.32)"
          : (failed > 0 ? "rgba(239,68,68,0.32)" : "rgba(148,163,184,0.30)")}`,
      }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        {allOk
          ? <CheckCircle2 size={22} color="#86efac" />
          : <AlertCircle size={22} color={failed > 0 ? "#fca5a5" : "#94a3b8"} />}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontSize: 14, fontWeight: 700,
            color: allOk ? "#86efac" : (failed > 0 ? "#fca5a5" : "#cbd5e1"),
          }}>
            {allOk ? "All findings fixed" : (failed > 0 ? "Completed with failures" : "Nothing to do")}
          </div>
          <div style={{
            marginTop: 2, fontSize: 11, color: "#94a3b8",
            fontFamily: "'JetBrains Mono', monospace",
          }}>
            {completed - failed} fixed · {failed} failed · {total} total
            {durationStr && <> · ⏱ {durationStr}</>}
          </div>
        </div>
      </div>
      {terminal?.message && (
        <div style={{
          fontSize: 11, color: "#cbd5e1", lineHeight: 1.5,
          fontFamily: "'JetBrains Mono', monospace",
        }}>{terminal.message}</div>
      )}
      <div style={{
        marginTop: 8, fontSize: 10, color: "#64748b",
        fontFamily: "'JetBrains Mono', monospace",
      }}>job <code>{jobId?.slice(0, 12)}</code></div>
    </div>
  );
}


export default function FixProgressDrawer() {
  const {
    jobId, total, status,
    completed, failed, remaining,
    completedRows, activeRow, terminal, error,
    startedAt, endedAt, lastEventAt, eventCount,
    hydrated, canRestart, restarting,
    panelVisible, hidePanel, restart,
  } = useFixJob();

  // 1-second tick for the running clock (only while drawer visible
  // AND job is running to avoid wasted renders).
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!panelVisible || status !== "running") return undefined;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [panelVisible, status]);

  // Escape closes the panel (UI only — does NOT cancel the job).
  useEffect(() => {
    if (!panelVisible) return undefined;
    const onKey = (e) => { if (e.key === "Escape") hidePanel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [panelVisible, hidePanel]);

  // Keep mounted in DOM regardless of state — when status is idle
  // we just render nothing tangible so React keeps the tree.
  const shouldRender = status !== "idle";

  const elapsedRef = endedAt ?? now;
  const elapsedMs  = startedAt ? elapsedRef - startedAt : 0;
  const elapsedStr = (() => {
    const s = Math.floor(elapsedMs / 1000);
    const m = Math.floor(s / 60);
    return `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  })();
  const idleMs = lastEventAt ? now - lastEventAt : 0;
  const pulseTone = (() => {
    if (terminal) return "done";
    if (idleMs < 2000)  return "alive";
    if (idleMs < 30000) return "slow";
    return "stuck";
  })();

  const pct = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
  const activeFixIndex = activeRow?.fix_index ?? (completedRows.length + 1);
  const activeFixTotal = activeRow?.fix_total ?? total;

  // Drawer sits ABOVE the persistent bar (44 px) — the visible part
  // of the drawer ends 44 px above the viewport bottom so the bar is
  // always reachable.
  const visibleStyle = panelVisible
    ? "translateY(0)"
    : "translateY(calc(100% - 0px))"; // slide fully out behind the bar

  if (!shouldRender) return null;

  return (
    <>
      {/* Backdrop only when visible — click = HIDE not cancel. */}
      {panelVisible && (
        <div
          data-testid="fix-progress-scrim"
          onClick={hidePanel}
          style={{
            position: "fixed", inset: 0, zIndex: 1295,
            background: "rgba(6,8,13,0.55)",
            backdropFilter: "blur(4px)",
          }}
        />
      )}
      <aside
        data-testid="fix-progress-drawer"
        data-visible={panelVisible ? "true" : "false"}
        style={{
          position: "fixed",
          // Sits ABOVE the 44 px persistent bar.
          top: 0, right: 0, bottom: 44,
          width: "min(620px, 100%)", zIndex: 1301,
          background: "#0d1018",
          borderLeft: "1px solid rgba(255,255,255,0.08)",
          color: "#e8ecf3",
          display: "flex", flexDirection: "column",
          boxShadow: panelVisible ? "-20px 0 60px rgba(0,0,0,0.55)" : "none",
          // Iter 212m-148: slide via transform — component NEVER unmounts.
          transform: panelVisible ? "translateX(0)" : "translateX(110%)",
          transition: "transform 280ms cubic-bezier(0.4, 0, 0.2, 1), box-shadow 280ms ease-out",
          // Use the deeper translateY hide on small screens (mobile)
          // so the drawer slides down from the top of the bar rather
          // than off the side. Disabled here in favour of right-slide;
          // the spec is for desktop primarily.
          pointerEvents: panelVisible ? "auto" : "none",
          visibility: visibleStyle ? undefined : "hidden",
        }}>
        <style>{`
          @keyframes pulseDot {
            0%, 100% { opacity: 0.4; }
            50%      { opacity: 1; }
          }
          @keyframes diffLineIn {
            from { opacity: 0; transform: translateY(4px); }
            to   { opacity: 1; transform: translateY(0); }
          }
          @keyframes progressShine {
            0%   { background-position: -200px 0; }
            100% { background-position: 200px 0; }
          }
          .anim-spin { animation: spin 1s linear infinite; }
          @keyframes spin { to { transform: rotate(360deg); } }
        `}</style>

        <header style={{
          padding: "16px 20px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          background: terminal
            ? (failed > 0
                ? "linear-gradient(90deg, rgba(239,68,68,0.10), transparent)"
                : "linear-gradient(90deg, rgba(34,197,94,0.10), transparent)")
            : "linear-gradient(90deg, rgba(249,115,22,0.10), transparent)",
          display: "flex", alignItems: "center", gap: 12,
        }}>
          {terminal
            ? (failed > 0 ? <ShieldAlert size={18} color="#fca5a5" />
                          : <ShieldCheck size={18} color="#86efac" />)
            : <Loader2 size={18} color="#fdba74" className="anim-spin" />}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 700,
                          display: "flex", alignItems: "center", gap: 10 }}>
              <span>{terminal ? "Fix complete" : "Fix in progress"}</span>
              <span
                data-testid={`fix-progress-pulse-${pulseTone}`}
                style={{
                  width: 8, height: 8, borderRadius: 999,
                  background: pulseTone === "alive" ? "#86efac"
                            : pulseTone === "slow"  ? "#fde68a"
                            : pulseTone === "stuck" ? "#fca5a5"
                            : "#475569",
                  animation: pulseTone === "alive"
                    ? "pulseDot 1.0s infinite" : (
                    pulseTone === "slow"  ? "pulseDot 2.0s infinite" : "none"
                  ),
                }}
              />
            </div>
            <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2,
                          fontFamily: "'JetBrains Mono', monospace",
                          display: "flex", alignItems: "center", gap: 10,
                          flexWrap: "wrap" }}>
              <span data-testid="fix-progress-counter">
                {terminal
                  ? `${completed - failed} fixed · ${failed} failed · ${total} total`
                  : `${completed}/${total} · ${remaining} remaining`}
              </span>
              <span data-testid="fix-progress-clock"
                    style={{ color: terminal ? "#94a3b8" : "#fdba74" }}>
                ⏱ {elapsedStr}
              </span>
              <span data-testid="fix-progress-events">
                {eventCount} events
              </span>
              {!terminal && pulseTone === "slow" && (
                <span style={{ color: "#fde68a" }}>still working…</span>
              )}
              {!terminal && pulseTone === "stuck" && (
                <span style={{ color: "#fca5a5" }}>
                  connection slow — {Math.floor(idleMs / 1000)}s idle
                </span>
              )}
            </div>
          </div>
          {/* Hide button — chevron-down conveys "slide back down" intent. */}
          <button
            data-testid="fix-progress-hide"
            onClick={hidePanel}
            title="Hide (job keeps running)"
            style={{
              padding: 6, background: "transparent",
              border: "1px solid rgba(255,255,255,0.12)",
              borderRadius: 6, cursor: "pointer", color: "#94a3b8",
              display: "inline-flex", alignItems: "center", gap: 4,
            }}>
            <ChevronDown size={14} />
            <span style={{ fontSize: 11 }}>Hide</span>
          </button>
          {/* Legacy close icon kept for muscle-memory but it ALSO hides
              now (never cancels). */}
          <button
            data-testid="fix-progress-close"
            onClick={hidePanel}
            style={{
              padding: 6, background: "transparent",
              border: "1px solid rgba(255,255,255,0.12)",
              borderRadius: 6, cursor: "pointer", color: "#94a3b8",
            }}>
            <X size={14} />
          </button>
        </header>

        {/* Progress bar */}
        <div style={{
          height: 4, background: "rgba(255,255,255,0.04)",
          position: "relative", overflow: "hidden",
        }}>
          <div
            data-testid="fix-progress-bar"
            style={{
              width: `${pct}%`, height: "100%",
              background: terminal && failed > 0
                ? "linear-gradient(90deg, #fca5a5, #f87171, #fca5a5)"
                : terminal
                  ? "linear-gradient(90deg, #4ade80, #86efac, #4ade80)"
                  : "linear-gradient(90deg, #fb923c, #fdba74, #fb923c)",
              backgroundSize: "200px 100%",
              animation: terminal ? "none" : "progressShine 1.6s linear infinite",
              transition: "width 280ms ease-out",
            }} />
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: "auto", padding: 16 }}>
          {error && (
            <div style={{
              padding: 12, marginBottom: 12, borderRadius: 8,
              background: "rgba(239,68,68,0.08)",
              border: "1px solid rgba(239,68,68,0.4)",
              color: "#fca5a5", fontSize: 12,
            }}>{error}</div>
          )}

          {terminal && (
            <FinalSummaryCard
              terminal={terminal}
              completed={completed}
              failed={failed}
              total={total}
              durationStr={elapsedStr}
              jobId={jobId}
            />
          )}

          {!terminal && activeRow && (
            <ActiveFixCard
              row={activeRow}
              fixIndex={activeFixIndex}
              fixTotal={activeFixTotal}
            />
          )}

          {!terminal && !activeRow && completedRows.length === 0 && !error && (
            <div style={{
              display: "flex", alignItems: "center", gap: 10,
              color: "#94a3b8", fontSize: 12, padding: "8px 0",
            }}>
              <span style={{
                width: 8, height: 8, borderRadius: 999,
                background: "#fb923c", animation: "pulseDot 1.4s infinite",
              }} />
              Waiting for first event…
            </div>
          )}

          {completedRows.length > 0 && (
            <div data-testid="fix-completed-list">
              <div style={{
                fontSize: 10, color: "#64748b",
                fontFamily: "'JetBrains Mono', monospace",
                letterSpacing: "0.06em", textTransform: "uppercase",
                marginBottom: 8, marginTop: terminal ? 0 : 14,
              }}>
                Completed · {completedRows.length}
              </div>
              {completedRows.map((r) => (
                <CompletedRow key={r.finding_id || r.index} row={r} />
              ))}
            </div>
          )}
        </div>

        {terminal && (
          <footer
            data-testid="fix-progress-footer"
            style={{
              padding: "12px 20px",
              borderTop: "1px solid rgba(255,255,255,0.06)",
              background: "rgba(255,255,255,0.015)",
              fontSize: 12, color: "#94a3b8",
              display: "flex", justifyContent: "space-between", alignItems: "center",
              gap: 12, flexWrap: "wrap",
            }}>
            <span data-testid="fix-progress-terminal">
              {hydrated && (
                <span style={{
                  marginRight: 8, padding: "2px 6px", borderRadius: 999,
                  background: "rgba(56,189,248,0.10)",
                  border: "1px solid rgba(56,189,248,0.30)",
                  color: "#7dd3fc", fontSize: 10,
                }}>RESUMED</span>
              )}
              {completed - failed} of {total} {total === 1 ? "fix" : "fixes"} committed
            </span>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              {canRestart && (
                <button
                  data-testid="fix-progress-restart"
                  onClick={restart}
                  disabled={restarting}
                  style={{
                    padding: "6px 14px", borderRadius: 6,
                    border: "1px solid rgba(251,146,60,0.40)",
                    background: restarting
                      ? "rgba(251,146,60,0.05)"
                      : "rgba(251,146,60,0.12)",
                    color: "#fdba74",
                    cursor: restarting ? "wait" : "pointer",
                    fontSize: 12, fontWeight: 600,
                    display: "inline-flex", alignItems: "center", gap: 6,
                  }}>
                  <RotateCw size={12} className={restarting ? "anim-spin" : ""} />
                  {restarting ? "Restarting…" : "Restart remaining"}
                </button>
              )}
              <button
                data-testid="fix-progress-done"
                onClick={hidePanel}
                style={{
                  padding: "6px 14px", borderRadius: 6,
                  border: "1px solid rgba(255,255,255,0.18)",
                  background: "rgba(255,255,255,0.04)",
                  color: "#e8ecf3", cursor: "pointer", fontSize: 12,
                }}>Hide</button>
            </div>
          </footer>
        )}

        {!terminal && canRestart && (
          <div
            data-testid="fix-progress-mid-error-restart"
            style={{
              padding: "10px 16px",
              borderTop: "1px solid rgba(239,68,68,0.20)",
              background: "rgba(239,68,68,0.05)",
              fontSize: 12,
              display: "flex", justifyContent: "space-between",
              alignItems: "center", gap: 12, flexWrap: "wrap",
            }}>
            <span style={{ color: "#fca5a5" }}>
              Worker crashed — restart to retry the remaining findings.
            </span>
            <button
              data-testid="fix-progress-mid-error-restart-btn"
              onClick={restart}
              disabled={restarting}
              style={{
                padding: "6px 12px", borderRadius: 6,
                border: "1px solid rgba(251,146,60,0.40)",
                background: "rgba(251,146,60,0.12)",
                color: "#fdba74",
                cursor: restarting ? "wait" : "pointer",
                fontSize: 12, fontWeight: 600,
                display: "inline-flex", alignItems: "center", gap: 6,
              }}>
              <RotateCw size={12} className={restarting ? "anim-spin" : ""} />
              {restarting ? "Restarting…" : "Restart"}
            </button>
          </div>
        )}
      </aside>
    </>
  );
}
