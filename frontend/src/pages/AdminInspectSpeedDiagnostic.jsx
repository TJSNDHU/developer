/**
 * pages/AdminInspectSpeedDiagnostic.jsx — Iter 314
 *
 * ZERO-MUTATION read-only wrapper for
 *   GET /api/aurem-dev/admin/speed-diagnostic?window_days=<n>&sample=<n>
 *
 * PURPOSE: Founder hit a JWT wall when navigating directly to the raw
 * admin endpoint (browser session token lives in localStorage, not
 * cookies — direct nav can't attach it). This page uses the same
 * axios instance every other admin surface uses via `getSpeedDiagnostic`,
 * so the JWT is attached automatically and the founder can pull the
 * report without ever touching credentials.
 *
 * SCOPE (deliberately narrow, mirrors AdminInspectLoop):
 *   • Route: /admin/inspect-speed-diagnostic
 *   • Reads ONLY. Buttons:
 *       - Refresh (re-hits GET with the current form values)
 *       - Copy JSON (clipboard, no mutation)
 *       - Close (react-router navigate back)
 *     No api.post / api.put / api.delete in this file — grep-auditable.
 *   • Form inputs: `window_days` (int, 1-180, default 30),
 *     `sample` (int, 1-100, default 20). Bounds match the backend
 *     clamps in routers/admin.py so an out-of-range value from this
 *     UI never surprises the server.
 *   • No auto-refresh — user pulls fresh data explicitly.
 */
import React, { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getSpeedDiagnostic } from "../lib/loopApi";

const C = {
  bg:       "#0a0e18",
  panel:    "#111827",
  border:   "#1f2937",
  text:     "#e6ebf3",
  dim:      "#9aa0a8",
  faint:    "#6b7280",
  green:    "#34d399",
  amber:    "#f5a524",
  red:      "#f87171",
  mono:     "ui-monospace, SFMono-Regular, Menlo, monospace",
};

function Card({ title, testid, right, children }) {
  return (
    <div
      data-testid={testid}
      style={{
        background: C.panel, border: `1px solid ${C.border}`,
        borderRadius: 8, padding: "12px 14px", marginBottom: 12,
      }}
    >
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 8,
      }}>
        <div style={{
          fontFamily: C.mono, fontSize: 11, letterSpacing: "0.14em",
          color: C.dim,
        }}>{title}</div>
        {right || null}
      </div>
      {children}
    </div>
  );
}

function Pre({ value, testid, maxHeight = 480 }) {
  return (
    <pre
      data-testid={testid}
      style={{
        background: C.bg, border: `1px dashed ${C.border}`, borderRadius: 6,
        padding: 10, margin: 0, color: C.text,
        fontFamily: C.mono, fontSize: 11, lineHeight: 1.5,
        maxHeight, overflow: "auto",
        whiteSpace: "pre-wrap", wordBreak: "break-word",
      }}
    >{value}</pre>
  );
}

function Btn({ children, onClick, testid, kind = "default" }) {
  const styles = kind === "primary"
    ? { background: "#1e3a8a", borderColor: "#3b82f6", color: "#e0e7ff" }
    : { background: "#1f2937", borderColor: C.border, color: C.text };
  return (
    <button
      type="button"
      data-testid={testid}
      onClick={onClick}
      style={{
        padding: "6px 12px",
        fontSize: 11, fontFamily: C.mono, letterSpacing: "0.06em",
        borderRadius: 5, cursor: "pointer",
        border: "1px solid", ...styles,
      }}
    >{children}</button>
  );
}

export default function AdminInspectSpeedDiagnostic() {
  const navigate = useNavigate();
  const [windowDays, setWindowDays] = useState(30);
  const [sample, setSample]         = useState(20);
  const [data, setData]             = useState(null);
  const [err, setErr]               = useState(null);
  const [loading, setLoading]       = useState(false);
  const [fetchedAt, setFetchedAt]   = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const resp = await getSpeedDiagnostic({
        windowDays: Number(windowDays) || 30,
        sample:     Number(sample)     || 20,
      });
      setData(resp);
      setFetchedAt(new Date().toISOString());
    } catch (e) {
      const detail = e?.response?.data?.detail
        || e?.message
        || "Failed to fetch speed-diagnostic report";
      setErr(typeof detail === "string" ? detail : JSON.stringify(detail));
    } finally {
      setLoading(false);
    }
  }, [windowDays, sample]);

  const copyJson = useCallback(async () => {
    if (!data) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    } catch {
      // clipboard permission denied — silently skip; the pre block is
      // already selectable manually.
    }
  }, [data]);

  return (
    <div
      data-testid="admin-inspect-speed-diagnostic"
      style={{
        minHeight: "100vh", background: C.bg, color: C.text,
        padding: "24px 20px", fontFamily: C.mono, fontSize: 12,
      }}
    >
      <div style={{ maxWidth: 980, margin: "0 auto" }}>
        {/* Header */}
        <div style={{
          display: "flex", justifyContent: "space-between",
          alignItems: "center", marginBottom: 16,
        }}>
          <div>
            <div style={{
              fontSize: 10, letterSpacing: "0.18em",
              color: C.dim, marginBottom: 4,
            }}>ITER 309 · SPEED DIAGNOSTIC · READ-ONLY</div>
            <div style={{ fontSize: 18, color: C.text }}>
              Per-phase wall-clock report
            </div>
          </div>
          <Btn testid="speed-diag-close" onClick={() => navigate(-1)}>
            ← Close
          </Btn>
        </div>

        {/* Query form */}
        <Card title="QUERY" testid="speed-diag-query-card"
              right={
                <Btn testid="speed-diag-refresh-btn"
                     kind="primary"
                     onClick={load}>
                  {loading ? "Loading…" : (data ? "Refresh" : "Run report")}
                </Btn>
              }>
          <div style={{
            display: "grid", gridTemplateColumns: "1fr 1fr",
            gap: 12, alignItems: "end",
          }}>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ color: C.dim, fontSize: 10, letterSpacing: "0.14em" }}>
                WINDOW_DAYS (1-180, default 30)
              </span>
              <input
                data-testid="speed-diag-window-days"
                type="number" min={1} max={180}
                value={windowDays}
                onChange={(e) => setWindowDays(e.target.value)}
                style={{
                  background: C.bg, border: `1px solid ${C.border}`,
                  color: C.text, padding: "6px 8px", borderRadius: 5,
                  fontFamily: C.mono, fontSize: 12,
                }}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ color: C.dim, fontSize: 10, letterSpacing: "0.14em" }}>
                SAMPLE (1-100, default 20)
              </span>
              <input
                data-testid="speed-diag-sample"
                type="number" min={1} max={100}
                value={sample}
                onChange={(e) => setSample(e.target.value)}
                style={{
                  background: C.bg, border: `1px solid ${C.border}`,
                  color: C.text, padding: "6px 8px", borderRadius: 5,
                  fontFamily: C.mono, fontSize: 12,
                }}
              />
            </label>
          </div>
        </Card>

        {/* Error */}
        {err && (
          <Card title="ERROR" testid="speed-diag-err-card">
            <div data-testid="speed-diag-err"
                 style={{ color: C.red, whiteSpace: "pre-wrap" }}>
              {err}
            </div>
          </Card>
        )}

        {/* Result */}
        {data && !err && (
          <Card
            title={`RESULT · fetched at ${fetchedAt || "—"}`}
            testid="speed-diag-result-card"
            right={
              <Btn testid="speed-diag-copy-btn" onClick={copyJson}>
                Copy JSON
              </Btn>
            }
          >
            <Pre
              testid="speed-diag-json"
              value={JSON.stringify(data, null, 2)}
              maxHeight={520}
            />
          </Card>
        )}

        {!data && !err && !loading && (
          <div data-testid="speed-diag-empty"
               style={{ color: C.faint, textAlign: "center", padding: "40px 0" }}>
            Enter window &amp; sample, then click <b>Run report</b>.
          </div>
        )}
      </div>
    </div>
  );
}
