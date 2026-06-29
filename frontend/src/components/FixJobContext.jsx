/**
 * FixJobContext.jsx — Iter 212m-148
 *
 * GLOBAL persistent state for the Bulk Fix job pipeline. Replaces the
 * panel-local state that lived inside FixProgressDrawer.jsx.
 *
 * KEY ARCHITECTURE WIN:
 *   The SSE connection is owned by THIS provider — NOT by the drawer.
 *   So when the user clicks outside, hits Escape, navigates to another
 *   page, or just hides the drawer, the EventSource STAYS OPEN and the
 *   job keeps streaming.  Only `all_done` (server-confirmed terminal)
 *   or an explicit cancel/dismiss kills the stream.
 *
 * Founder spec ("fix(fix-panel): persistent job state — SSE global,
 * panel hide-only, bar always visible until dismissed"):
 *   - panelVisible ↔ UI state only
 *   - Backdrop click / Escape → hidePanel() (NOT cancel)
 *   - PersistentFixBar reads this context and never disappears until
 *     the user dismisses the terminal state
 *   - Job state survives unmounts, route changes, panel toggles
 */
import React, {
  createContext, useContext, useEffect, useRef, useState, useCallback,
} from "react";
import { api } from "../lib/api";

const LS_JOB_KEY = "aurem_fix_active_job";

/* ── Public context shape ────────────────────────────────────────── */
const Ctx = createContext(null);
export function useFixJob() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useFixJob must be used inside <FixJobProvider>");
  return v;
}

/* ── Provider ────────────────────────────────────────────────────── */
export function FixJobProvider({ children }) {
  // Job identity.
  const [jobId,    setJobId]    = useState(null);
  const [total,    setTotal]    = useState(0);
  // Live state.
  const [items,    setItems]    = useState({});    // by finding_id
  const [activeId, setActiveId] = useState(null);  // currently-streaming finding
  const [terminal, setTerminal] = useState(null);
  const [error,    setError]    = useState(null);
  // Restart UX.
  const [canRestart, setCanRestart] = useState(false);
  const [hydrated,   setHydrated]   = useState(false);
  // UI visibility — separate from job state so the bar can persist
  // while the panel is hidden.
  const [panelVisible, setPanelVisible] = useState(false);
  // dismissed → bar disappears even though we still have terminal state.
  const [dismissed,    setDismissed]    = useState(false);
  // Clock + heartbeat.
  const [startedAt,   setStartedAt]   = useState(null);
  const [endedAt,     setEndedAt]     = useState(null);
  const [lastEventAt, setLastEventAt] = useState(null);
  const [eventCount,  setEventCount]  = useState(0);

  const esRef = useRef(null);

  /* ── Derived ──────────────────────────────────────────────────── */
  const allRows = Object.values(items).sort(
    (a, b) => (a.index || 0) - (b.index || 0),
  );
  const completedRows = allRows.filter((r) => r.phase === "fix-done");
  const completed = terminal?.completed ?? completedRows.length;
  const failed    = terminal?.failed    ?? completedRows.filter((r) => r.ok === false).length;
  const remaining = Math.max(0, (total || 0) - completed);
  const status = (() => {
    if (!jobId) return "idle";
    if (terminal) return failed > 0 ? "error" : "done";
    if (error) return "error";
    return "running";
  })();
  const activeRow = activeId ? items[activeId] : null;

  /* ── Public actions ───────────────────────────────────────────── */
  const startJob = useCallback(({ job_id, total: t }) => {
    if (!job_id) return;
    // Close any prior stream cleanly.
    if (esRef.current) {
      try { esRef.current.close(); } catch { /* ignore */ }
      esRef.current = null;
    }
    setJobId(job_id);
    setTotal(t || 1);
    setItems({});
    setActiveId(null);
    setTerminal(null);
    setError(null);
    setCanRestart(false);
    setHydrated(false);
    setDismissed(false);
    setPanelVisible(true);
    const t0 = Date.now();
    setStartedAt(t0);
    setEndedAt(null);
    setLastEventAt(t0);
    setEventCount(0);
    try { localStorage.setItem(LS_JOB_KEY,
      JSON.stringify({ job_id, total: t || 1 }));
    } catch { /* ignore */ }
  }, []);

  const showPanel = useCallback(() => setPanelVisible(true),  []);
  const hidePanel = useCallback(() => setPanelVisible(false), []);
  const togglePanel = useCallback(() => setPanelVisible((v) => !v), []);

  /* dismiss = hide the persistent bar AND clear the job (only meaningful
   * in terminal states). */
  const dismiss = useCallback(() => {
    setDismissed(true);
    setPanelVisible(false);
    if (esRef.current) {
      try { esRef.current.close(); } catch { /* ignore */ }
      esRef.current = null;
    }
    try { localStorage.removeItem(LS_JOB_KEY); } catch { /* ignore */ }
  }, []);

  /* cancel = nuke the in-flight job (currently UI-only — backend has
   * no /cancel route for fix jobs yet; this just closes our SSE and
   * marks the job aborted locally). */
  const cancel = useCallback(() => {
    if (esRef.current) {
      try { esRef.current.close(); } catch { /* ignore */ }
      esRef.current = null;
    }
    setTerminal({
      phase: "done", ok: false, message: "Cancelled by user.",
    });
    setEndedAt(Date.now());
  }, []);

  const [restarting, setRestarting] = useState(false);
  const restart = useCallback(async () => {
    if (!jobId || restarting) return;
    setRestarting(true);
    setError(null);
    try {
      const r = await api.post(`/fix-pipeline/restart/${jobId}`);
      const next = r.data || {};
      if (next.nothing_to_do) {
        setTerminal({
          phase: "done", ok: true,
          message: next.message || "All findings already complete.",
        });
        try { localStorage.removeItem(LS_JOB_KEY); } catch { /* ignore */ }
        return;
      }
      startJob({ job_id: next.job_id, total: next.remaining });
    } catch (e) {
      const msg = e?.response?.data?.detail?.message
                  || e?.response?.data?.detail
                  || e?.message
                  || "Restart failed";
      setError(typeof msg === "string" ? msg : JSON.stringify(msg));
    } finally {
      setRestarting(false);
    }
  }, [jobId, restarting, startJob]);

  /* ── Mount: re-attach to any in-flight job (page-refresh recovery) ── */
  useEffect(() => {
    try {
      const raw = localStorage.getItem(LS_JOB_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (!parsed?.job_id) return;
      // Re-attach silently — DO NOT auto-open the panel on a page
      // refresh.  The bar will surface and the user can open the
      // panel if they want.
      setJobId(parsed.job_id);
      setTotal(parsed.total || 1);
      setItems({});
      setActiveId(null);
      setTerminal(null);
      setError(null);
      setCanRestart(false);
      setHydrated(false);
      setDismissed(false);
      const t0 = Date.now();
      setStartedAt(t0);
      setEndedAt(null);
      setLastEventAt(t0);
      setEventCount(0);
    } catch { /* corrupt cache — ignore */ }
  }, []);

  /* ── Global event hook — let any component fire the open event ── */
  useEffect(() => {
    const onOpen = (e) => {
      const { job_id, total: t } = e.detail || {};
      if (!job_id) return;
      startJob({ job_id, total: t || 1 });
    };
    window.addEventListener("aurem:open-fix-progress", onOpen);
    return () => window.removeEventListener("aurem:open-fix-progress", onOpen);
  }, [startJob]);

  /* ── SSE — owned by the provider, NOT the panel ──────────────── */
  useEffect(() => {
    if (!jobId) return undefined;
    // Tear down any stale stream.
    if (esRef.current) {
      try { esRef.current.close(); } catch { /* ignore */ }
      esRef.current = null;
    }

    const base =
      (typeof process !== "undefined" && process.env && process.env.REACT_APP_BACKEND_URL) ||
      window.location.origin;
    const token = localStorage.getItem("aurem_token");
    const url = `${base}/api/aurem-dev/fix-pipeline/stream/${jobId}?token=${encodeURIComponent(token || "")}`;
    let es;
    try { es = new EventSource(url, { withCredentials: false }); }
    catch (e) { setError(String(e)); return undefined; }
    esRef.current = es;

    const handlePhase = (ev) => {
      let data;
      try { data = JSON.parse(ev.data); }
      catch { return; }
      setLastEventAt(Date.now());
      setEventCount((c) => c + 1);
      const fid = data.finding_id;
      const ph  = data.phase;

      if (fid && ph && ph !== "done" && ph !== "job-start"
          && ph !== "heartbeat" && ph !== "gone" && ph !== "fix-done"
          && ph !== "hydrated" && ph !== "job-error") {
        setActiveId(fid);
      }

      if (fid && ph !== "done" && ph !== "job-start"
          && ph !== "heartbeat" && ph !== "gone"
          && ph !== "hydrated" && ph !== "job-error") {
        setItems((s) => {
          const prev = s[fid] || {};
          const merged = { ...prev, ...data, phase: ph };
          if (ph === "retrying") {
            merged.attempt     = data.attempt;
            merged.attempts_of = data.of ?? prev.attempts_of;
            merged.last_error  = data.last_error;
          }
          if (ph === "fix-diff" && Array.isArray(data.diff)) {
            merged.diff = data.diff;
          }
          return { ...s, [fid]: merged };
        });
      }

      if (ph === "fix-done" && data.ok === true) {
        try {
          window.dispatchEvent(new CustomEvent("aurem:finding-fixed", {
            detail: {
              finding_id: data.finding_id,
              rule_id:    data.rule_id,
              commit_sha: data.commit_sha,
              html_url:   data.html_url,
              file:       data.file,
            },
          }));
        } catch { /* ignore */ }
      }
      if (ph === "fix-done") {
        setActiveId((prev) => (prev === fid ? null : prev));
      }

      if (ph === "done") {
        setTerminal(data);
        setEndedAt(Date.now());
        setActiveId(null);
        if (data.status && data.status !== "done") {
          setCanRestart(data.can_restart !== false);
        }
        try { es.close(); } catch { /* ignore */ }
        esRef.current = null;
        try { localStorage.removeItem(LS_JOB_KEY); } catch { /* ignore */ }
      }
      if (ph === "job-error") {
        setError(data.message || data.error || "Worker error");
        setCanRestart(data.can_restart !== false);
      }
      if (ph === "hydrated") {
        setHydrated(true);
        setTerminal({
          phase:     "done",
          ok:        data.status === "done",
          completed: data.completed || 0,
          failed:    data.failed || 0,
          total:     data.total || 0,
          results:   data.results || [],
          message:   data.message || "Resumed from history.",
          status:    data.status,
        });
        setEndedAt(Date.now());
        const itemsFromResults = {};
        (data.results || []).forEach((r, idx) => {
          itemsFromResults[r.finding_id || `idx_${idx}`] = {
            phase:      "fix-done",
            finding_id: r.finding_id,
            ok:         r.ok,
            commit_sha: r.commit_sha,
            html_url:   r.html_url,
            file:       r.file,
            rule_id:    r.rule_id,
            error:      r.error,
            index:      idx,
          };
        });
        setItems(itemsFromResults);
        setCanRestart(data.can_restart === true);
        try { es.close(); } catch { /* ignore */ }
        esRef.current = null;
      }
      if (ph === "gone") {
        setError(data.message || "Job not found (may have expired)");
        setCanRestart(data.can_restart === true);
        try { es.close(); } catch { /* ignore */ }
        esRef.current = null;
        try { localStorage.removeItem(LS_JOB_KEY); } catch { /* ignore */ }
      }
    };

    es.addEventListener("phase", handlePhase);
    es.onerror = () => {
      if (es.readyState === 2) {
        setError("Connection lost");
      }
    };
    // CRITICAL: cleanup runs ONLY when jobId changes (i.e. user
    // explicitly starts a new job or dismisses).  Component unmounts
    // (route changes, drawer hide/show) DO NOT trigger this cleanup
    // because the provider lives at App root and never unmounts.
    return () => { try { es.close(); } catch { /* ignore */ } };
  }, [jobId]);

  const value = {
    // identity
    jobId, total,
    // derived state
    status, terminal, error, hydrated, canRestart,
    completed, failed, remaining,
    allRows, completedRows, activeRow,
    // timing
    startedAt, endedAt, lastEventAt, eventCount,
    // UI
    panelVisible, dismissed,
    showPanel, hidePanel, togglePanel,
    // actions
    startJob, dismiss, cancel, restart, restarting,
    // raw items for advanced consumers
    items,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
