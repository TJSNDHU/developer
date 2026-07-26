/**
 * pages/AdminInspectLoop.jsx — Iter 309 · Batch-2 aftermath
 *
 * ZERO-MUTATION read-only inspect view for a specific loop.
 * Promoted from backlog to P1 after the 2026-07-26 incident where a
 * diagnostic-looking chat-surface button silently mutated state.
 *
 * SCOPE (deliberately narrow):
 *   • Route: /admin/inspect-loop/:loopId
 *   • Reads ONLY. The only "buttons" on this page are:
 *       - Refresh   (re-hits the read-only GET endpoint)
 *       - Close     (react-router navigate back)
 *     Neither writes to any store, dispatches an event, or sends a
 *     chat turn. Auditable by grep for `api.post` / `api.put` /
 *     `api.delete` in this file → must return zero matches.
 *   • Data source: `GET /admin/loop-inspect/{loop_id}` which returns
 *     { session, events_tail, sse_buffer }.
 *
 * DESIGN NOTES:
 *   • Uses <pre> blocks for the raw session doc and events. Reading
 *     JSON is easier than a lossy table for post-mortem work.
 *   • No auto-refresh — user pulls fresh data explicitly. Prevents
 *     the page from becoming "helpfully" reactive during inspection.
 */
import React, { useCallback, useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { getLoopInspect } from "../lib/loopApi";

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

function Pre({ value, testid, maxHeight = 320 }) {
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

export default function AdminInspectLoop() {
  const { loopId } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [err, setErr]   = useState(null);
  const [loading, setLoading] = useState(false);
  const [lastFetched, setLastFetched] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const resp = await getLoopInspect(loopId, 50);
      setData(resp);
      setLastFetched(new Date());
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || "inspect failed");
    } finally {
      setLoading(false);
    }
  }, [loopId]);

  useEffect(() => { load(); }, [load]);

  const session = data?.session;
  const events  = Array.isArray(data?.events_tail) ? data.events_tail : [];
  const sseBuf  = data?.sse_buffer;

  return (
    <div
      data-testid="admin-inspect-loop"
      style={{
        minHeight: "100vh", background: C.bg, color: C.text,
        padding: "24px 28px", fontFamily: "system-ui, -apple-system, sans-serif",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 14 }}>
        <button
          type="button"
          data-testid="inspect-loop-close"
          onClick={() => navigate(-1)}
          style={{
            appearance: "none", background: "transparent",
            border: `1px solid ${C.border}`, borderRadius: 6,
            color: C.dim, fontFamily: C.mono, fontSize: 11,
            padding: "4px 10px", cursor: "pointer",
          }}
        >← Back</button>
        <h1 style={{
          margin: 0, fontSize: 22, letterSpacing: "-0.01em", color: C.amber,
        }}>Inspect Loop</h1>
        <span style={{
          fontFamily: C.mono, fontSize: 12, color: C.dim,
        }}>{loopId}</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 10, color: C.faint, fontFamily: C.mono }}>
          {lastFetched ? `fetched ${lastFetched.toLocaleTimeString()}` : "…"}
        </span>
        <button
          type="button"
          data-testid="inspect-loop-refresh"
          onClick={load}
          disabled={loading}
          style={{
            appearance: "none", background: "transparent",
            border: `1px solid ${C.border}`, borderRadius: 6,
            color: loading ? C.faint : C.text,
            fontFamily: C.mono, fontSize: 11,
            padding: "4px 10px",
            cursor: loading ? "wait" : "pointer",
          }}
        >{loading ? "⟳ refreshing…" : "⟳ Refresh"}</button>
      </div>

      <div style={{
        marginBottom: 14, fontSize: 11, color: C.faint, lineHeight: 1.55,
      }}>
        Read-only inspection view — the only actions available on this page are Refresh and Back.
        Nothing here mutates loop state, sends chat, or triggers side effects. Safe to leave open
        while a live loop runs.
      </div>

      {err && (
        <Card testid="inspect-loop-error" title="ERROR">
          <div style={{ color: C.red, fontFamily: C.mono, fontSize: 12 }}>{err}</div>
        </Card>
      )}

      <Card
        testid="inspect-loop-session-card"
        title="LOOP SESSION"
        right={session ? (
          <span style={{ fontFamily: C.mono, fontSize: 11, color: C.green }}>
            state · {session.state} · phase · {session.phase || "?"}
          </span>
        ) : null}
      >
        {session ? (
          <>
            <div style={{ fontFamily: C.mono, fontSize: 11, color: C.dim, marginBottom: 8 }}>
              project_id · <span style={{ color: C.text }}>{session.project_id || "—"}</span>
              {" · updated_at · "}
              <span style={{ color: C.text }}>{session.updated_at || "—"}</span>
              {" · user_id · "}
              <span style={{ color: C.text }}>{(session.user_id || "").slice(0, 12)}…</span>
            </div>
            <Pre
              testid="inspect-loop-session-json"
              value={JSON.stringify(session, null, 2)}
              maxHeight={420}
            />
          </>
        ) : loading ? (
          <div style={{ color: C.dim, fontSize: 12 }}>loading…</div>
        ) : (
          <div style={{ color: C.dim, fontSize: 12 }}>no session</div>
        )}
      </Card>

      <Card
        testid="inspect-loop-sse-card"
        title="SSE RING BUFFER (per-loop)"
      >
        {sseBuf ? (
          <Pre
            testid="inspect-loop-sse-json"
            value={JSON.stringify(sseBuf, null, 2)}
            maxHeight={160}
          />
        ) : (
          <div style={{ color: C.faint, fontSize: 11, fontFamily: C.mono }}>
            No ring-buffer entry for this loop_id on this pod. This is expected if the loop
            already reached a terminal state (buffer TTL-evicts within ~10 min) OR if the loop
            is running on a different worker pod than this /admin request landed on.
          </div>
        )}
      </Card>

      <Card
        testid="inspect-loop-events-card"
        title={`EVENTS TAIL (last ${events.length}, newest first)`}
      >
        {events.length ? (
          <Pre
            testid="inspect-loop-events-json"
            value={events.map((e, i) =>
              `[${i}] ${e.ts || "?"} · seq=${e.seq ?? "?"} · state=${e.state || "?"} · phase=${e.phase || "?"}\n${JSON.stringify(e, null, 2)}`
            ).join("\n\n")}
            maxHeight={500}
          />
        ) : (
          <div style={{ color: C.dim, fontSize: 12 }}>
            {loading ? "loading…" : "no events"}
          </div>
        )}
      </Card>
    </div>
  );
}
