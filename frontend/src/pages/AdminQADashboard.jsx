/**
 * AdminQADashboard.jsx — Iter 303
 *
 * /admin/qa — founder-facing QA health dashboard. Locks:
 *
 *   - Test counts (backend Pytest AST-authoritative, Vitest, Playwright)
 *   - Test-style ratio (STATIC_GREP % + weak-P0 count vs CI threshold)
 *   - a11y baseline health (component + journey surfaces)
 *   - CI job status per named workflow (or honest "unavailable" if
 *     GITHUB_ACTIONS_TOKEN + GITHUB_REPO env not set)
 *
 * Every number is fetched live from `/api/aurem-dev/admin/qa/status`
 * on mount and on manual refresh — no cached/stale numbers in the UI.
 *
 * Admin-gated (backend rejects with 403). Reads the founder JWT from
 * localStorage under the `aurem_admin_token` key — same pattern as
 * every other /admin/* page in this app.
 */
import React, { useEffect, useState, useCallback } from "react";
import axios from "axios";

const API = process.env.REACT_APP_BACKEND_URL;


function Card({ title, sub, testid, children }) {
  return (
    <div data-testid={testid}
          style={{
            background: "#0f0f0f",
            border: "1px solid #262626",
            borderRadius: 12,
            padding: 22,
            fontFamily: "system-ui, -apple-system, sans-serif",
          }}>
      <div style={{
        fontSize: 11, letterSpacing: 1.2, color: "#666",
        textTransform: "uppercase", marginBottom: 8,
      }}>{title}</div>
      {sub && <div style={{ fontSize: 12, color: "#888",
                             marginBottom: 12 }}>{sub}</div>}
      {children}
    </div>
  );
}


function BigNumber({ value, label }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
      <div style={{ fontSize: 32, fontWeight: 700, color: "#e5e5e5",
                     fontVariantNumeric: "tabular-nums" }}>{value}</div>
      <div style={{ fontSize: 12, color: "#666" }}>{label}</div>
    </div>
  );
}


function CIJobChip({ name, job }) {
  const conc = job?.conclusion;
  const status = job?.status;
  let bg = "#333", fg = "#aaa", label = "unknown";
  if (conc === "success")        { bg = "#0a2416"; fg = "#4ade80"; label = "pass"; }
  else if (conc === "failure")   { bg = "#2b0a0a"; fg = "#f87171"; label = "fail"; }
  else if (conc === "cancelled") { bg = "#2b230a"; fg = "#facc15"; label = "cancelled"; }
  else if (status === "in_progress") { bg = "#0a1e2b"; fg = "#60a5fa"; label = "running"; }
  else if (status === "queued")  { bg = "#1a1a1a"; fg = "#94a3b8"; label = "queued"; }
  return (
    <a href={job?.html_url || "#"} target="_blank" rel="noreferrer"
        data-testid={`ci-job-${name}`}
        data-ci-conclusion={conc || status || "unknown"}
        style={{
          display: "flex", justifyContent: "space-between",
          alignItems: "center", padding: "10px 12px",
          background: bg, borderRadius: 8, textDecoration: "none",
          fontSize: 12, color: fg, marginBottom: 6,
          border: "1px solid rgba(255,255,255,0.05)",
        }}>
      <span style={{ fontFamily: "monospace" }}>{name}</span>
      <span style={{ textTransform: "uppercase",
                      fontSize: 10, letterSpacing: 1 }}>{label}</span>
    </a>
  );
}


export default function AdminQADashboard() {
  const [data,    setData]    = useState(null);
  const [error,   setError]   = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const tok = localStorage.getItem("aurem_admin_token")
        || localStorage.getItem("aurem_token");
      const r = await axios.get(
        `${API}/api/aurem-dev/admin/qa/status`,
        { headers: { Authorization: `Bearer ${tok || ""}` } },
      );
      setData(r.data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || "unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading && !data) {
    return (
      <div style={{ padding: 40, color: "#666",
                     fontFamily: "system-ui" }}
            data-testid="admin-qa-loading">
        Loading QA status…
      </div>
    );
  }

  if (error && !data) {
    return (
      <div style={{ padding: 40, color: "#f87171",
                     fontFamily: "system-ui" }}
            data-testid="admin-qa-error">
        <div style={{ fontSize: 14, marginBottom: 8 }}>
          Failed to load QA status
        </div>
        <div style={{ fontSize: 12, color: "#888" }}>{String(error)}</div>
        <button onClick={load}
                 style={{ marginTop: 12, padding: "6px 12px",
                           background: "#1a1a1a", color: "#e5e5e5",
                           border: "1px solid #333", borderRadius: 6,
                           cursor: "pointer" }}>Retry</button>
      </div>
    );
  }

  const counts = data.test_counts;
  const style  = data.test_style;
  const a11y   = data.a11y;
  const ci     = data.ci_status;
  return (
    <div data-testid="admin-qa-dashboard"
          style={{ minHeight: "100vh", padding: "32px 40px",
                    background: "#0a0a0a", color: "#e5e5e5",
                    fontFamily: "system-ui" }}>
      {/* Header row */}
      <div style={{ display: "flex", justifyContent: "space-between",
                     alignItems: "center", marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>
            QA Health
          </h1>
          <div style={{ fontSize: 11, color: "#666", marginTop: 4 }}>
            Live from `/api/admin/qa/status` — generated {" "}
            {new Date(data.generated_at * 1000).toLocaleTimeString()}
            {" · "}{data.took_ms}ms
          </div>
        </div>
        <button onClick={load} disabled={loading}
                 data-testid="admin-qa-refresh"
                 style={{ padding: "8px 16px", background: "#1a1a1a",
                           color: "#e5e5e5", border: "1px solid #333",
                           borderRadius: 6, cursor: "pointer",
                           opacity: loading ? 0.5 : 1 }}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {/* 4-card grid */}
      <div style={{ display: "grid",
                     gridTemplateColumns: "1fr 1fr",
                     gap: 20, maxWidth: 1100 }}>

        {/* CARD 1 — Test counts */}
        <Card testid="admin-qa-card-counts"
              title="Test counts"
              sub={`grand total: ${counts.grand_total_tests}`}>
          <div style={{ display: "grid",
                          gridTemplateColumns: "1fr 1fr",
                          gap: 14, marginTop: 8 }}>
            <BigNumber value={counts.backend_pytest.tests}
                        label={`backend (${counts.backend_pytest.files} files)`} />
            <BigNumber value={counts.frontend_vitest.tests}
                        label={`vitest (${counts.frontend_vitest.files} files)`} />
            <BigNumber value={counts.playwright.tests}
                        label={`playwright (${counts.playwright.files} files)`} />
            <BigNumber value={counts.reasoning_evals.tests}
                        label={`reasoning evals`} />
          </div>
        </Card>

        {/* CARD 2 — Test-style ratio */}
        <Card testid="admin-qa-card-style"
              title="Test-style ratio (iter289 guard)"
              sub={style.available
                    ? `${style.total_tests} tests analysed · CI threshold ${style.threshold_pct}%`
                    : "analyser unavailable"}>
          {style.available ? (
            <>
              <BigNumber value={`${style.ratio_pct}%`}
                          label="STATIC_GREP" />
              <div style={{ marginTop: 10, display: "flex", gap: 16,
                              fontSize: 12, color: "#888" }}>
                <span>weak_p0: <strong style={{
                  color: style.weak_p0_count === 0 ? "#4ade80" :
                          (style.weak_p0_count > 15 ? "#f87171" : "#facc15"),
                }}
                   data-testid="admin-qa-weak-p0">
                  {style.weak_p0_count}</strong></span>
                <span>threshold: <strong style={{
                  color: style.passes_threshold ? "#4ade80" : "#f87171",
                }}>
                  {style.passes_threshold ? "PASS" : "FAIL"}</strong></span>
              </div>
            </>
          ) : (
            <div style={{ fontSize: 12, color: "#888" }}>
              {style.reason}
            </div>
          )}
        </Card>

        {/* CARD 3 — a11y baselines */}
        <Card testid="admin-qa-card-a11y"
              title="a11y baseline"
              sub="charter L3 burn-down">
          <div style={{ display: "grid",
                          gridTemplateColumns: "1fr 1fr",
                          gap: 14 }}>
            {Object.entries(a11y).map(([kind, v]) => (
              <div key={kind}>
                <div style={{ fontSize: 11, color: "#666",
                                textTransform: "uppercase",
                                marginBottom: 4 }}>{kind}</div>
                {v.available ? (
                  <>
                    <BigNumber value={v.total_known_violations}
                                label={`known violations`} />
                    <div style={{ fontSize: 11, color: "#888",
                                    marginTop: 4 }}>
                      {v.surfaces_clean}/{v.surfaces_tracked} clean
                    </div>
                  </>
                ) : (
                  <div style={{ fontSize: 12, color: "#888" }}>
                    unavailable
                  </div>
                )}
              </div>
            ))}
          </div>
          {a11y.journeys?.available && (
            <details style={{ marginTop: 14, fontSize: 12,
                                color: "#888" }}>
              <summary style={{ cursor: "pointer" }}>
                per-surface violations
              </summary>
              <pre style={{ background: "#0a0a0a",
                             padding: 10, marginTop: 8,
                             borderRadius: 4, overflow: "auto" }}>
{JSON.stringify(a11y.journeys.per_surface, null, 2)}
              </pre>
            </details>
          )}
        </Card>

        {/* CARD 4 — CI status */}
        <Card testid="admin-qa-card-ci"
              title="CI status (GitHub Actions)"
              sub={ci.available
                    ? `run ${ci.workflow_run_id} · ${(ci.commit_sha || "").slice(0,7)}`
                    : "not wired"}>
          {ci.available ? (
            <>
              {ci.commit_message && (
                <div style={{ fontSize: 12, color: "#888",
                                marginBottom: 12,
                                whiteSpace: "nowrap",
                                overflow: "hidden",
                                textOverflow: "ellipsis" }}>
                  “{(ci.commit_message || "").split("\n")[0]}”
                </div>
              )}
              {Object.entries(ci.jobs).map(([name, job]) => (
                <CIJobChip key={name} name={name} job={job} />
              ))}
              <a href={ci.workflow_url} target="_blank" rel="noreferrer"
                  style={{ fontSize: 11, color: "#60a5fa",
                            textDecoration: "none", marginTop: 8,
                            display: "inline-block" }}>
                open workflow run →
              </a>
            </>
          ) : (
            <div style={{ fontSize: 12, color: "#888",
                            lineHeight: 1.6 }}
                  data-testid="admin-qa-ci-unavailable">
              <div style={{ color: "#facc15", marginBottom: 6 }}>
                CI status not wired
              </div>
              {ci.reason}
            </div>
          )}
        </Card>
      </div>

      {/* Iter 334 — Auto-QA agent latest report */}
      <LatestAutoQASection />
    </div>
  );
}


// Iter 334 — renders .emergent/latest-qa-report.md served by
// GET /admin/qa/latest-report. Raw markdown in a <pre> (no
// react-markdown dependency in this repo — deliberate, documented).
function LatestAutoQASection() {
  const [report, setReport] = useState(null);
  useEffect(() => {
    const tok = localStorage.getItem("aurem_admin_token")
      || localStorage.getItem("aurem_token");
    axios.get(`${API}/api/aurem-dev/admin/qa/latest-report`,
      { headers: { Authorization: `Bearer ${tok || ""}` } })
      .then((r) => setReport(r.data))
      .catch((e) => setReport({
        error: e?.response?.data?.detail || e.message,
      }));
  }, []);
  if (!report) return null;
  return (
    <div className="latest-auto-qa" data-testid="admin-qa-latest-report"
          style={{ maxWidth: 1100, marginTop: 20 }}>
      <Card testid="admin-qa-card-auto-report"
            title="Latest Auto-QA Report"
            sub={report.modified_at
              ? `written ${new Date(report.modified_at * 1000).toLocaleString()}`
              : "auto-qa-agent job output"}>
        {report.content ? (
          <pre data-testid="admin-qa-report-content"
                style={{ whiteSpace: "pre-wrap", fontSize: 11.5,
                          lineHeight: 1.6, color: "#d4d4d4",
                          fontFamily: "'JetBrains Mono', monospace",
                          maxHeight: 420, overflowY: "auto",
                          margin: 0 }}>
            {report.content}
          </pre>
        ) : (
          <div data-testid="admin-qa-report-empty"
                style={{ fontSize: 12, color: "#888" }}>
            {report.error || "No report yet — auto-qa-agent job has not run"}
          </div>
        )}
      </Card>
    </div>
  );
}
