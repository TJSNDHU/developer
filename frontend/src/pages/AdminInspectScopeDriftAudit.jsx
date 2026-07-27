/**
 * pages/AdminInspectScopeDriftAudit.jsx — Iter 314
 *
 * ZERO-MUTATION read-only wrapper for
 *   GET /api/aurem-dev/admin/scope-drift-audit?days=<n>&limit=<n>
 *
 * Same universal admin-inspect pattern as AdminInspectSpeedDiagnostic:
 * uses the shared `api` axios instance via `getScopeDriftAudit`, so
 * the browser JWT rides in the Authorization header automatically.
 * Founder no longer needs to hit the raw endpoint URL and hit the
 * "Authorization header missing" wall.
 *
 * SCOPE (deliberately narrow, mirrors AdminInspectLoop):
 *   • Route: /admin/inspect-scope-drift
 *   • Reads ONLY. Buttons: Refresh, Copy JSON, Close.
 *     No api.post / api.put / api.delete — grep-auditable.
 *   • Form inputs: `days` (int, 1-180, default 30), `limit` (int,
 *     1-500, default 50). Bounds match the backend clamps in
 *     routers/admin.py.
 */
import React, { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getScopeDriftAudit } from "../lib/loopApi";

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

export default function AdminInspectScopeDriftAudit() {
  const navigate = useNavigate();
  const [days, setDays]     = useState(30);
  const [limit, setLimit]   = useState(50);
  const [data, setData]     = useState(null);
  const [err, setErr]       = useState(null);
  const [loading, setLoad]  = useState(false);
  const [fetchedAt, setFetchedAt] = useState(null);

  const load = useCallback(async () => {
    setLoad(true);
    setErr(null);
    try {
      const resp = await getScopeDriftAudit({
        days:  Number(days)  || 30,
        limit: Number(limit) || 50,
      });
      setData(resp);
      setFetchedAt(new Date().toISOString());
    } catch (e) {
      const detail = e?.response?.data?.detail
        || e?.message
        || "Failed to fetch scope-drift audit";
      setErr(typeof detail === "string" ? detail : JSON.stringify(detail));
    } finally {
      setLoad(false);
    }
  }, [days, limit]);

  const copyJson = useCallback(async () => {
    if (!data) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    } catch {
      // clipboard permission denied — pre block is still selectable.
    }
  }, [data]);

  return (
    <div
      data-testid="admin-inspect-scope-drift"
      style={{
        minHeight: "100vh", background: C.bg, color: C.text,
        padding: "24px 20px", fontFamily: C.mono, fontSize: 12,
      }}
    >
      <div style={{ maxWidth: 980, margin: "0 auto" }}>
        <div style={{
          display: "flex", justifyContent: "space-between",
          alignItems: "center", marginBottom: 16,
        }}>
          <div>
            <div style={{
              fontSize: 10, letterSpacing: "0.18em",
              color: C.dim, marginBottom: 4,
            }}>ITER 311 · SCOPE-DRIFT AUDIT · READ-ONLY</div>
            <div style={{ fontSize: 18, color: C.text }}>
              Cross-loop file-selector drift evidence
            </div>
          </div>
          <Btn testid="scope-drift-close" onClick={() => navigate(-1)}>
            ← Close
          </Btn>
        </div>

        <Card title="QUERY" testid="scope-drift-query-card"
              right={
                <Btn testid="scope-drift-refresh-btn"
                     kind="primary"
                     onClick={load}>
                  {loading ? "Loading…" : (data ? "Refresh" : "Run audit")}
                </Btn>
              }>
          <div style={{
            display: "grid", gridTemplateColumns: "1fr 1fr",
            gap: 12, alignItems: "end",
          }}>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ color: C.dim, fontSize: 10, letterSpacing: "0.14em" }}>
                DAYS (1-180, default 30)
              </span>
              <input
                data-testid="scope-drift-days"
                type="number" min={1} max={180}
                value={days}
                onChange={(e) => setDays(e.target.value)}
                style={{
                  background: C.bg, border: `1px solid ${C.border}`,
                  color: C.text, padding: "6px 8px", borderRadius: 5,
                  fontFamily: C.mono, fontSize: 12,
                }}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ color: C.dim, fontSize: 10, letterSpacing: "0.14em" }}>
                LIMIT (1-500, default 50)
              </span>
              <input
                data-testid="scope-drift-limit"
                type="number" min={1} max={500}
                value={limit}
                onChange={(e) => setLimit(e.target.value)}
                style={{
                  background: C.bg, border: `1px solid ${C.border}`,
                  color: C.text, padding: "6px 8px", borderRadius: 5,
                  fontFamily: C.mono, fontSize: 12,
                }}
              />
            </label>
          </div>
        </Card>

        {err && (
          <Card title="ERROR" testid="scope-drift-err-card">
            <div data-testid="scope-drift-err"
                 style={{ color: C.red, whiteSpace: "pre-wrap" }}>
              {err}
            </div>
          </Card>
        )}

        {data && !err && (
          <Card
            title={`RESULT · fetched at ${fetchedAt || "—"}`}
            testid="scope-drift-result-card"
            right={
              <Btn testid="scope-drift-copy-btn" onClick={copyJson}>
                Copy JSON
              </Btn>
            }
          >
            <Pre
              testid="scope-drift-json"
              value={JSON.stringify(data, null, 2)}
              maxHeight={520}
            />
          </Card>
        )}

        {!data && !err && !loading && (
          <div data-testid="scope-drift-empty"
               style={{ color: C.faint, textAlign: "center", padding: "40px 0" }}>
            Enter days &amp; limit, then click <b>Run audit</b>.
          </div>
        )}
      </div>
    </div>
  );
}
