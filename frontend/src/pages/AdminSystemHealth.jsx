/**
 * pages/AdminSystemHealth.jsx — Iter 212m-205
 *
 * Permanent replacement for the "sab theek hai?" audit loop.  This
 * admin page polls three live signals every 30 s and renders them in
 * a single dashboard so the founder never has to ask again:
 *
 *   1. Deploy sync — fetches /api/aurem-dev/version from BOTH the
 *      current origin AND https://auremcto.com, then compares the
 *      commit hashes.  Mismatch → amber banner across the page.
 *
 *   2. Council A health — reuses /admin/council/health (the same
 *      endpoint the AdminOverview banner already reads).
 *
 *   3. ORA learning — new /admin/system-health/ora-learning endpoint
 *      returns live counts for the 3 learning layers (RAG rows,
 *      fine-tune queue, fix-recall storage).
 *
 * All fetches are additive (Promise.all).  Any one failure surfaces
 * inline; the other cards keep working.
 */
import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";

const REFRESH_MS = 30_000;
const PROD_ORIGIN = "https://auremcto.com";

const C = {
  bg:    "#050811",
  panel: "#0f131e",
  border:"#1f2937",
  text:  "#e5e7eb",
  dim:   "#94a3b8",
  faint: "#64748b",
  amber: "#f59e0b",
  green: "#22c55e",
  red:   "#ef4444",
  mono:  '"JetBrains Mono", ui-monospace, monospace',
};

const Card = ({ title, status, children, testid }) => (
  <div
    data-testid={testid}
    style={{
      background: C.panel,
      border: `1px solid ${C.border}`,
      borderRadius: 12,
      padding: 18,
    }}
  >
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
      <div style={{ fontFamily: C.mono, fontSize: 11, letterSpacing: "0.14em", color: C.dim, textTransform: "uppercase" }}>
        {title}
      </div>
      {status && (
        <span style={{
          padding: "3px 10px", borderRadius: 999,
          fontFamily: C.mono, fontSize: 10, letterSpacing: "0.08em",
          background: status.bg, color: status.color, border: `1px solid ${status.color}55`,
        }}>{status.label}</span>
      )}
    </div>
    {children}
  </div>
);

const StatusBadge = (state) => {
  if (state === "ok" || state === "active" || state === "ready" || state === "live" || state === true)
    return { label: "OK",       bg: "rgba(34,197,94,0.10)",  color: C.green };
  if (state === "warming" || state === "collecting" || state === "phase_1_storage_only" || state === "degraded")
    return { label: "WARMING",  bg: "rgba(245,158,11,0.10)", color: C.amber };
  return   { label: "DOWN",     bg: "rgba(239,68,68,0.10)",  color: C.red };
};

export default function AdminSystemHealth() {
  const [previewVer, setPreviewVer] = useState(null);
  const [prodVer, setProdVer]       = useState(null);
  const [council, setCouncil]       = useState(null);
  const [learning, setLearning]     = useState(null);
  const [loopMetrics, setLoopMetrics] = useState(null);
  const [errs, setErrs]             = useState({});
  const [lastRefresh, setLastRefresh] = useState(null);

  const fetchAll = useCallback(async () => {
    const nextErrs = {};

    // 1) Current origin /version (self)
    try {
      const r = await api.get("/version");
      setPreviewVer(r.data);
    } catch (e) { nextErrs.self_version = e?.message || "self /version failed"; }

    // 2) Production /version (cross-origin, no auth)
    try {
      const r = await fetch(`${PROD_ORIGIN}/api/aurem-dev/version`, { cache: "no-store" });
      if (r.ok) setProdVer(await r.json());
      else nextErrs.prod_version = `prod /version → HTTP ${r.status}`;
    } catch (e) { nextErrs.prod_version = e?.message || "prod /version fetch failed"; }

    // 3) Council A health
    try {
      const r = await api.get("/admin/council/health");
      setCouncil(r.data);
    } catch (e) { nextErrs.council = e?.response?.data?.detail || e?.message || "council failed"; }

    // 4) ORA learning status
    try {
      const r = await api.get("/admin/system-health/ora-learning");
      setLearning(r.data);
    } catch (e) { nextErrs.learning = e?.response?.data?.detail || e?.message || "learning failed"; }

    // 5) Iter 309 · Phase 0.2 — Loop metrics (post-Phase-0 impact
    // probe). Compares last-7d vs prior-7d failed_ratio so the
    // founder can eyeball whether the recent loop-engine rewrite
    // shifted the FAILED rate in real traffic without pasting
    // credentials into a terminal.
    try {
      const r = await api.get("/admin/loop-metrics");
      setLoopMetrics(r.data);
    } catch (e) { nextErrs.loop_metrics = e?.response?.data?.detail || e?.message || "loop-metrics failed"; }

    setErrs(nextErrs);
    setLastRefresh(new Date());
  }, []);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, REFRESH_MS);
    return () => clearInterval(id);
  }, [fetchAll]);

  const outOfSync =
    previewVer && prodVer
    && previewVer.commit_sha !== prodVer.commit_sha
    && previewVer.commit_sha !== "unknown";

  return (
    <div
      data-testid="admin-system-health"
      style={{
        minHeight: "100vh",
        background: C.bg,
        color: C.text,
        fontFamily: '-apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif',
        padding: "24px 28px 80px",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <div>
          <Link to="/admin/overview" style={{ fontFamily: C.mono, fontSize: 11, color: C.dim, textDecoration: "none" }}>
            ← Admin
          </Link>
          <h1 style={{ fontFamily: C.mono, fontSize: 22, fontWeight: 700, color: C.amber, margin: "4px 0 0", letterSpacing: "-0.5px" }}>
            System Health
          </h1>
          <div style={{ fontSize: 12, color: C.dim, marginTop: 4 }}>
            Live signals · refreshes every 30 s ·{" "}
            {lastRefresh ? `last: ${lastRefresh.toLocaleTimeString()}` : "loading…"}
          </div>
        </div>
        <button
          type="button"
          data-testid="system-health-refresh"
          onClick={fetchAll}
          style={{
            padding: "8px 14px", background: "transparent", border: `1px solid ${C.border}`,
            borderRadius: 8, color: C.dim, fontFamily: C.mono, fontSize: 11,
            letterSpacing: "0.06em", cursor: "pointer",
          }}
        >↻ REFRESH NOW</button>
      </div>

      {/* Out-of-sync banner */}
      {outOfSync && (
        <div
          data-testid="deploy-out-of-sync-banner"
          style={{
            marginBottom: 20,
            padding: "12px 16px",
            background: "rgba(245,158,11,0.10)",
            border: `1px solid ${C.amber}`,
            borderRadius: 10,
            display: "flex", alignItems: "center", gap: 12,
            fontFamily: C.mono, fontSize: 12,
          }}
        >
          <span style={{ fontSize: 20 }}>⚠</span>
          <div style={{ flex: 1, color: C.text }}>
            <b style={{ color: C.amber }}>Preview and production are out of sync.</b>{" "}
            Preview <code>{previewVer?.commit_sha}</code> ≠ production <code>{prodVer?.commit_sha}</code>.
            Redeploy required to ship recent changes to auremcto.com.
          </div>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 16 }}>

        {/* 1. Deploy sync card */}
        <Card
          testid="card-deploy-sync"
          title="Deploy Sync"
          status={outOfSync
            ? StatusBadge("degraded")
            : (previewVer && prodVer ? StatusBadge("ok") : StatusBadge("down"))}
        >
          {[
            { label: "PREVIEW", ver: previewVer, err: errs.self_version },
            { label: "PRODUCTION", ver: prodVer, err: errs.prod_version },
          ].map(({ label, ver, err }) => (
            <div key={label} style={{ marginBottom: 8, paddingBottom: 8, borderBottom: `1px dashed ${C.border}` }}>
              <div style={{ fontFamily: C.mono, fontSize: 9, letterSpacing: "0.14em", color: C.faint, marginBottom: 3 }}>
                {label}
              </div>
              {err ? (
                <div style={{ fontSize: 12, color: C.red }}>{err}</div>
              ) : ver ? (
                <>
                  <div style={{ fontFamily: C.mono, fontSize: 13, color: C.text }}>
                    <span style={{ color: C.amber }}>{ver.commit_sha}</span>
                  </div>
                  <div style={{ fontSize: 10, color: C.faint }}>
                    {ver.environment} · built {new Date(ver.built_at).toLocaleString()}
                  </div>
                </>
              ) : (
                <div style={{ fontSize: 12, color: C.faint }}>loading…</div>
              )}
            </div>
          ))}
        </Card>

        {/* 2. Council A card */}
        <Card
          testid="card-council-health"
          title="Council A"
          status={council ? StatusBadge(council?.live ? "ok" : "down") : StatusBadge("down")}
        >
          {errs.council ? (
            <div style={{ fontSize: 12, color: C.red }}>{errs.council}</div>
          ) : council ? (
            <>
              <Row label="Primary intended" value={council.primary_intended || "—"} />
              <Row label="Primary actual"   value={council.primary_actual || "—"} />
              <Row label="Live"             value={council.live ? "YES" : "NO"} color={council.live ? C.green : C.red} />
              <Row label="Last probe"       value={council.last_probe ? new Date(council.last_probe).toLocaleString() : "—"} />
            </>
          ) : <div style={{ fontSize: 12, color: C.faint }}>loading…</div>}
        </Card>

        {/* 3. ORA Learning card */}
        <Card
          testid="card-ora-learning"
          title="ORA Learning"
          status={learning ? StatusBadge(learning.layers?.layer_1_rag?.status) : StatusBadge("down")}
        >
          {errs.learning ? (
            <div style={{ fontSize: 12, color: C.red }}>{errs.learning}</div>
          ) : learning ? (
            <>
              <LayerRow
                label="Layer 1 · RAG few-shot"
                info={learning.layers?.layer_1_rag}
                progress={{ value: learning.layers?.layer_1_rag?.rows || 0, max: learning.layers?.layer_1_rag?.min_for_rag || 20 }}
              />
              <LayerRow
                label="Layer 2 · Fine-tune queue"
                info={learning.layers?.layer_2_finetune}
                progress={{ value: learning.layers?.layer_2_finetune?.rows || 0, max: learning.layers?.layer_2_finetune?.threshold || 1000 }}
              />
              <LayerRow
                label="Layer 3 · Fix recall"
                info={learning.layers?.layer_3_fix_recall}
              />
              <div style={{ marginTop: 10, fontSize: 10, color: C.faint }}>
                checked {new Date(learning.checked_at).toLocaleTimeString()}
              </div>
            </>
          ) : <div style={{ fontSize: 12, color: C.faint }}>loading…</div>}
        </Card>

        {/* 4. Iter 309 · Phase 0.2 — Loop metrics (Phase-0 rewrite impact probe).
            Compares failed_ratio for the last 7 days vs the prior 7
            days so we can eyeball whether the recent loop-engine
            rewrite (heartbeats + periodic reaper + MAX_PHASE_RESTARTS
            2→1) shifted real prod traffic. Gate for Cluster 1 fix
            prioritization: delta ≥ 5pp → treat as P0 regression;
            flat/negative → test-scope fixture-shape only. */}
        <Card
          testid="card-loop-metrics"
          title="Loop Metrics — Phase 0 impact"
          status={(() => {
            if (errs.loop_metrics || !loopMetrics) return StatusBadge("down");
            const d = loopMetrics.delta_failed_ratio;
            if (d === null || d === undefined) return StatusBadge("warming");
            return d > 0.05 ? StatusBadge("degraded") : StatusBadge("ok");
          })()}
        >
          {errs.loop_metrics ? (
            <div style={{ fontSize: 12, color: C.red }}>{errs.loop_metrics}</div>
          ) : loopMetrics ? (
            <>
              <Row
                label="last 7d — resolved"
                value={String(loopMetrics.current?.resolved ?? 0)}
              />
              <Row
                label="last 7d — failed"
                value={String(loopMetrics.current?.failed ?? 0)}
                color={
                  (loopMetrics.current?.failed ?? 0) > 0 ? C.amber : C.text
                }
              />
              <Row
                label="last 7d — failed_ratio"
                value={
                  loopMetrics.current?.failed_ratio === null
                    ? "n/a"
                    : (loopMetrics.current.failed_ratio * 100).toFixed(1) + "%"
                }
              />
              <Row
                label="prior 7d — resolved"
                value={String(loopMetrics.previous?.resolved ?? 0)}
              />
              <Row
                label="prior 7d — failed_ratio"
                value={
                  loopMetrics.previous?.failed_ratio === null
                    ? "n/a"
                    : (loopMetrics.previous.failed_ratio * 100).toFixed(1) + "%"
                }
              />
              <Row
                label="Δ failed_ratio"
                value={
                  loopMetrics.delta_failed_ratio === null ||
                  loopMetrics.delta_failed_ratio === undefined
                    ? "insufficient data"
                    : (loopMetrics.delta_failed_ratio >= 0 ? "+" : "") +
                      (loopMetrics.delta_failed_ratio * 100).toFixed(1) +
                      "pp"
                }
                color={
                  loopMetrics.delta_failed_ratio === null ||
                  loopMetrics.delta_failed_ratio === undefined
                    ? C.faint
                    : loopMetrics.delta_failed_ratio > 0.05
                    ? C.red
                    : loopMetrics.delta_failed_ratio > 0
                    ? C.amber
                    : C.green
                }
              />
              <div style={{ marginTop: 10, fontSize: 10, color: C.faint, lineHeight: 1.4 }}>
                Gate: Δ &gt; +5pp → real prod regression (P0). Flat/negative → test-scope only.
              </div>
            </>
          ) : (
            <div style={{ fontSize: 12, color: C.faint }}>loading…</div>
          )}
        </Card>
      </div>
    </div>
  );
}

const Row = ({ label, value, color }) => (
  <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", fontSize: 12, fontFamily: C.mono }}>
    <span style={{ color: C.dim }}>{label}</span>
    <span style={{ color: color || C.text }}>{value}</span>
  </div>
);

const LayerRow = ({ label, info, progress }) => (
  <div style={{ padding: "8px 0", borderBottom: `1px dashed ${C.border}` }}>
    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
      <span style={{ color: C.text, fontFamily: C.mono }}>{label}</span>
      <span style={{ fontFamily: C.mono, color: StatusBadge(info?.status).color }}>
        {info?.status || "?"}
      </span>
    </div>
    {progress && (
      <div style={{ marginTop: 5 }}>
        <div style={{ height: 5, background: "#0a0e18", borderRadius: 3, overflow: "hidden" }}>
          <div style={{
            width: `${Math.min(100, (progress.value / progress.max) * 100)}%`,
            height: "100%",
            background: progress.value >= progress.max ? C.green : C.amber,
            transition: "width 300ms",
          }} />
        </div>
        <div style={{ marginTop: 3, fontSize: 10, color: C.faint, fontFamily: C.mono }}>
          {progress.value} / {progress.max}
        </div>
      </div>
    )}
    {info?.note && (
      <div style={{ marginTop: 5, fontSize: 10, color: C.faint }}>{info.note}</div>
    )}
  </div>
);
