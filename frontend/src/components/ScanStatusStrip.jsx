/**
 * components/ScanStatusStrip.jsx  —  Directive Session 3 · Part D
 *
 * Below-composer notification strip that surfaces scan state
 * WITHOUT ever being permanently visible. Three states, exactly:
 *
 *   1. Idle              — nothing rendered (default).
 *   2. Scan in progress  — live indicator during an active scan.
 *   3. Result            — post-scan critical/high summary (session-
 *                          scoped, one-shot) OR backlog reminder
 *                          (30-day idle, once/week cadence, capped
 *                          at 4 exposures per finding).
 *
 * Data source: `GET /findings/backlog?project_id=…` returns the
 * eligible list plus the policy decision (`should_show_strip` +
 * `reason`) so we do no policy math client-side.
 *
 * Contract with parent:
 *   • `projectId` prop — hides strip when null.
 *   • `scanState`  prop — `"idle" | "in_progress" | "just_completed"`.
 *   • `justCompletedSummary` prop — { critical, high, project_name }
 *     rendered while state is `just_completed` (session-scoped via
 *     sessionStorage — cleared on tab close).
 *   • `onReviewFindings()` — user clicked the "Review findings" CTA;
 *     parent opens the findings drawer.
 */
import React, { useCallback, useEffect, useState } from "react";
import { AlertTriangle, X, ChevronRight, Loader2 } from "lucide-react";
import { api } from "../lib/api";

// Session-scoped storage key for the "just completed" state so a
// closed tab clears it. Distinct from any localStorage keys — that's
// the whole point of Directive Part D §3.
const JUST_COMPLETED_KEY = "aurem_scan_just_completed";

// Storage limits the strip payload we cache so a huge finding list
// doesn't bloat sessionStorage.
const _MAX_CACHED_SUMMARY_JSON = 8_000;

/** Session-scoped read of the "scan just completed" state. */
function readJustCompleted() {
  try {
    const raw = sessionStorage.getItem(JUST_COMPLETED_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed?.expires_at && parsed.expires_at < Date.now()) {
      sessionStorage.removeItem(JUST_COMPLETED_KEY);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

/** Session-scoped write — parent may call this after a scan finishes.
 *  Exported for direct parent invocation; the strip itself reads
 *  from storage every render. */
export function markScanJustCompleted({ critical, high, projectId, projectName }) {
  try {
    if (!critical && !high) {
      // No critical/high — do not surface a strip, silently move to backlog.
      sessionStorage.removeItem(JUST_COMPLETED_KEY);
      return;
    }
    const payload = {
      critical:      Number(critical) || 0,
      high:          Number(high) || 0,
      project_id:    projectId,
      project_name:  projectName || "",
      created_at:    Date.now(),
      // Cap at the current tab session (browsers clear sessionStorage
      // on tab close anyway); the explicit 4-h TTL guards against a
      // long-lived pinned tab surfacing a stale strip forever.
      expires_at:    Date.now() + 4 * 60 * 60 * 1000,
    };
    const json = JSON.stringify(payload);
    if (json.length < _MAX_CACHED_SUMMARY_JSON) {
      sessionStorage.setItem(JUST_COMPLETED_KEY, json);
    }
  } catch { /* private mode / quota — silent */ }
}

/** Explicit clear — parent may call this after user acts on the strip. */
export function clearScanJustCompleted() {
  try { sessionStorage.removeItem(JUST_COMPLETED_KEY); } catch {
    // Private-mode / disabled storage — nothing to clear, ignore.
  }
}

// ────────────────────────────────────────────────────────────────
// The strip itself
// ────────────────────────────────────────────────────────────────
export default function ScanStatusStrip({
  projectId,
  scanState = "idle",
  onReviewFindings,
  // Optional: parent can pre-supply the project name (breadcrumb).
  projectName = "",
}) {
  const [backlog, setBacklog]   = useState(null);
  const [dismissing, setDismissing] = useState(false);
  // Force-hide when user X's the strip in this render pass so we
  // don't wait for the /backlog poll to reflect the dismiss.
  const [locallyHidden, setLocallyHidden] = useState(false);
  const justCompleted = readJustCompleted();

  // Poll backlog when project changes or when scan finishes.
  const refreshBacklog = useCallback(async () => {
    if (!projectId) return;
    try {
      const r = await api.get(`/findings/backlog?project_id=${encodeURIComponent(projectId)}`);
      setBacklog(r.data || null);
    } catch (_e) {
      // Silent — offline / auth blip should never crash the composer.
      setBacklog(null);
    }
  }, [projectId]);

  useEffect(() => {
    setLocallyHidden(false);
    refreshBacklog();
  }, [refreshBacklog, scanState]);

  // ── State machine — pick which render branch is active ──────────
  // Priority: in_progress (live) > just_completed (session) > backlog reminder.
  if (!projectId || locallyHidden) return null;

  if (scanState === "in_progress") {
    return (
      <StripShell
        tone="progress"
        data-testid="scan-strip-in-progress"
        icon={<Loader2 className="size-3.5 animate-spin" />}
        label={
          <span>
            Scan running{projectName && (
              <> · <span className="font-medium">{projectName}</span></>
            )}
          </span>
        }
      />
    );
  }

  // State 2 — just-completed result (session-scoped).
  if (justCompleted && scanState !== "in_progress"
      && justCompleted.project_id === projectId
      && (justCompleted.critical > 0 || justCompleted.high > 0)) {
    return (
      <StripShell
        tone={justCompleted.critical > 0 ? "critical" : "high"}
        data-testid="scan-strip-just-completed"
        icon={<AlertTriangle className="size-3.5" />}
        label={
          <span>
            {justCompleted.critical > 0
              ? <><b>{justCompleted.critical} critical</b>{justCompleted.high ? ` · ${justCompleted.high} high` : ""}</>
              : <><b>{justCompleted.high} high</b></>
            }
            {" · "}
            <span className="opacity-80">
              {justCompleted.project_name || projectName || "current project"}
            </span>
          </span>
        }
        cta={
          <button
            type="button"
            data-testid="scan-strip-review-cta"
            onClick={() => { clearScanJustCompleted(); onReviewFindings?.(); }}
            className="text-[11px] font-medium hover:underline"
          >
            Review findings →
          </button>
        }
        onDismiss={() => { clearScanJustCompleted(); setLocallyHidden(true); }}
      />
    );
  }

  // State 3 — backlog reminder (server decides eligibility).
  if (backlog?.should_show_strip
      && (backlog.critical_count > 0 || backlog.high_count > 0)) {
    return (
      <StripShell
        tone={backlog.critical_count > 0 ? "critical" : "high"}
        data-testid="scan-strip-backlog"
        icon={<AlertTriangle className="size-3.5" />}
        label={
          <span>
            {backlog.critical_count > 0 && (
              <><b>{backlog.critical_count} critical</b>{backlog.high_count > 0 ? ` · ${backlog.high_count} high` : ""}</>
            )}
            {backlog.critical_count === 0 && backlog.high_count > 0 && (
              <><b>{backlog.high_count} high</b></>
            )}
            {" idle 30+ days · "}
            <span className="opacity-80">
              {projectName || backlog.project_id}
            </span>
          </span>
        }
        cta={
          <button
            type="button"
            data-testid="scan-strip-backlog-review-cta"
            onClick={() => {
              // Stamp exposure server-side BEFORE opening the drawer
              // so the once-per-week cadence + 4-exposure cap
              // arithmetic is honest. Fire-and-forget — a network
              // hiccup on this call shouldn't block the review UX.
              const ids = (backlog.eligible || [])
                .map((f) => f.finding_id).filter(Boolean);
              if (ids.length) {
                api.post("/findings/expose-batch", {
                  project_id:  projectId,
                  finding_ids: ids.slice(0, 100),
                }).catch(() => { /* silent — cadence just resumes next poll */ });
              }
              onReviewFindings?.();
            }}
            className="text-[11px] font-medium hover:underline"
          >
            Review backlog →
          </button>
        }
        onDismiss={async () => {
          if (dismissing) return;
          setDismissing(true);
          setLocallyHidden(true);
          try {
            await api.post("/findings/dismiss", {
              project_id:       projectId,
              finding_batch_id: backlog.batch_id,
            });
          } catch { /* silent — 24 h retry-window means one blip is fine */ }
          setDismissing(false);
        }}
      />
    );
  }

  return null;
}

// ────────────────────────────────────────────────────────────────
// Presentational shell — the visual chrome is intentionally identical
// across the 3 states so the user perceives ONE consistent surface
// rather than 3 different affordances.
// ────────────────────────────────────────────────────────────────
function StripShell({ tone, icon, label, cta, onDismiss, ...rest }) {
  const toneCls = tone === "critical"
    ? "bg-red-500/10 border-red-500/30 text-red-200"
    : tone === "high"
      ? "bg-amber-500/10 border-amber-500/30 text-amber-200"
      : "bg-primary/10 border-primary/30 text-primary";
  return (
    <div
      className={
        "flex items-center gap-2 rounded-md border px-3 py-1.5 " +
        "text-[11px] " + toneCls
      }
      role="status"
      aria-live="polite"
      {...rest}
    >
      <span className="shrink-0">{icon}</span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {cta}
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          data-testid="scan-strip-dismiss"
          aria-label="Dismiss for 24 hours"
          className="ml-1 shrink-0 rounded p-0.5 opacity-70 hover:bg-white/10 hover:opacity-100"
        >
          <X className="size-3" />
        </button>
      )}
    </div>
  );
}
