/**
 * SecurityScanDrawer.jsx — Iter 212m-55
 *
 * Right-side slide-in drawer that runs the static vulnerability
 * scanner against the active project's connected GitHub repo and
 * lists findings, grouped by severity.
 *
 * Caching strategy (per founder spec — option 3c):
 *   • First open per project    → live scan
 *   • Subsequent opens          → cached (5 min TTL), show "Re-scan" CTA
 *   • Manual "Re-scan" button   → forces fresh fetch
 *
 * No auto-apply, by design — findings only. The user reads the list
 * and asks ORA in chat to fix what matters.
 */
import React, { useEffect, useState, useCallback } from "react";
import {
  X, ShieldCheck, ShieldAlert, Loader2, RefreshCw, FileWarning,
  Sparkles, GitPullRequest, ChevronDown, ChevronRight, ExternalLink,
  Wrench, CheckCircle2,
} from "lucide-react";
import { api } from "../lib/api";
import { getCachedScan, setCachedScan } from "../lib/securityScanCache";
import { toast } from "sonner";
import SecretScanCard from "./SecretScanCard";
import BulkFixConfirmModal from "./BulkFixConfirmModal";
import useFixQuota from "../lib/useFixQuota";

const CACHE_TTL_MS = 5 * 60 * 1000;

const SEV_ORDER = ["critical", "high", "medium", "low"];
const SEV_COLORS = {
  critical: { bg: "rgba(239,68,68,0.10)", fg: "#fca5a5", border: "rgba(239,68,68,0.45)" },
  high:     { bg: "rgba(249,115,22,0.10)", fg: "#fdba74", border: "rgba(249,115,22,0.45)" },
  medium:   { bg: "rgba(250,204,21,0.10)", fg: "#fde68a", border: "rgba(250,204,21,0.42)" },
  low:      { bg: "rgba(125,211,252,0.10)", fg: "#bae6fd", border: "rgba(125,211,252,0.42)" },
  // Iter 212m-129 — "Other" tile catches findings whose severity is
  // null / unset / not one of the 4 standard buckets (e.g. info,
  // unknown, '').  Without this the per-severity tiles total can
  // come up short of `Fix all N →` button count.
  other:    { bg: "rgba(148,163,184,0.08)", fg: "#cbd5e1", border: "rgba(148,163,184,0.30)" },
};

// Pill chip style used by the two-round stats strip (Iter 212m-66).
const _chipStyle = (border, fg) => ({
  padding: "2px 8px", borderRadius: 999,
  background: "rgba(255,255,255,0.02)",
  border: `1px solid ${border}`, color: fg,
});

export default function SecurityScanDrawer({ open, onClose, projectId, projectLabel, repoOwner, repoName }) {
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState(null);
  const [data, setData]         = useState(null);
  const [cachedAt, setCachedAt] = useState(null);
  // Iter 212m-66 — opt-in Vanguard 2.0 flags. Persisted per-browser
  // so a user's preference survives reload. The cache key embeds
  // the flags so a deep-mode result never overwrites a quick-mode
  // result for the same project (and vice versa).
  const [twoRound, setTwoRound] = useState(() => {
    try { return localStorage.getItem("aurem_scan_two_round") === "1"; }
    catch { return false; }
  });
  const [autoPr, setAutoPr] = useState(() => {
    try { return localStorage.getItem("aurem_scan_auto_pr") === "1"; }
    catch { return false; }
  });
  const [reportOpen, setReportOpen] = useState(false);
  // Iter 212m-121 — bulk fix modal state
  const [bulkOpen, setBulkOpen] = useState(false);
  // Iter 212m-190 — task-quota gating: vanguard fixes need Starter+,
  // bulk fix is Team-only. 1 successful fix = 1 task.
  const { quota } = useFixQuota();
  const canFix = !!quota && (quota.fix_tools || []).includes("vanguard-scan");
  const canBulk = canFix && !!quota?.bulk_fix;

  // Iter 212m-114 — Track which findings have been successfully fixed
  // in this session so we dim them + show a green ✓ instead of the
  // Fix button. Keyed by `${file}:${line}:${rule_id}`.
  const [fixedKeys, setFixedKeys] = useState({});
  // Track the currently-applying finding so we can disable the button
  // and show a spinner. Single concurrent fix.
  const [fixingKey, setFixingKey] = useState(null);

  const findingKey = (f) => `${f.file}:${f.line}:${f.rule_id}`;

  async function handleApplyFix(f) {
    if (!projectId) {
      toast.error("No active project — connect a repo first.");
      return;
    }
    const k = findingKey(f);
    if (fixedKeys[k] || fixingKey) return;
    setFixingKey(k);
    try {
      const res = await api.post("/security-scan/fix", {
        project_id: projectId,
        finding: {
          rule_id:  f.rule_id,
          file:     f.file,
          line:     f.line,
          severity: f.severity,
          title:    f.desc || f.rule_id,
          message:  f.desc || "",
          snippet:  f.snippet || "",
        },
      });
      const payload = res?.data || res;
      if (payload?.ok) {
        setFixedKeys((prev) => ({
          ...prev,
          [k]: {
            commit_sha: payload.commit_sha,
            html_url:   payload.html_url,
          },
        }));
        toast.success(
          `Fixed ${f.rule_id} — commit ${payload.commit_sha}`,
          { duration: 6000 },
        );
      } else {
        toast.error(payload?.message || "Fix failed");
      }
    } catch (e) {
      const detail = e?.response?.data?.detail;
      const code   = typeof detail === "object" ? detail.error : detail;
      if (code === "patch_did_not_resolve_finding") {
        toast.error(
          "AI patch did not resolve the finding — tokens refunded, no commit pushed.",
          { duration: 8000 },
        );
      } else if (code === "insufficient_tokens") {
        toast.error(
          `Insufficient tokens (need ${detail.needed}, you have ${detail.balance}).`,
        );
      } else if (code === "github_credentials_missing" || code === "github_unauthorized") {
        toast.error("Connect your GitHub PAT or OAuth before applying fixes.");
      } else {
        toast.error(typeof detail === "string" ? detail
                     : detail?.message || e?.message || "Fix failed");
      }
    } finally {
      setFixingKey(null);
    }
  }

  // Cache key: project + mode → so the two_round result has its own
  // 5-minute TTL slot independent of the legacy single-round one.
  const cacheKey = projectId
    ? `${projectId}::${twoRound ? "deep" : "fast"}${autoPr ? "+pr" : ""}`
    : null;

  const fetchScan = useCallback(async (force = false) => {
    if (!projectId) return;
    if (!force) {
      const hit = getCachedScan(cacheKey);
      if (hit && Date.now() - hit.at < CACHE_TTL_MS) {
        setData(hit.data);
        setCachedAt(hit.at);
        setError(null);
        return;
      }
    }
    setLoading(true);
    setError(null);
    try {
      const body = { project_id: projectId };
      if (twoRound) body.two_round = true;
      if (autoPr)   body.auto_pr   = true;
      const res = await api.post("/security-scan/run", body, {
        // Iter 212m-102 — Vanguard deep scan can take 30-60s on big repos;
        // bump axios timeout to 120s so CF edge timeouts surface as the
        // real error instead of an axios-side abort that looks like
        // "client disconnected".
        timeout: 120000,
      });
      const payload = res?.data || res;
      setCachedScan(cacheKey, payload);
      setData(payload);
      setCachedAt(Date.now());
      // Auto-expand the report panel when a fresh deep scan returned
      // an actionable AI report.
      if (payload?.remediation_report?.findings?.length) {
        setReportOpen(true);
      }
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || "Scan failed";
      setError(typeof msg === "string" ? msg : "Scan failed");
    } finally {
      setLoading(false);
    }
  }, [projectId, twoRound, autoPr, cacheKey]);

  // Persist flag prefs.
  useEffect(() => {
    try { localStorage.setItem("aurem_scan_two_round", twoRound ? "1" : "0"); }
    catch { /* ignore quota errors */ }
  }, [twoRound]);
  useEffect(() => {
    try { localStorage.setItem("aurem_scan_auto_pr", autoPr ? "1" : "0"); }
    catch { /* ignore quota errors */ }
  }, [autoPr]);

  // Auto-fetch on open.
  useEffect(() => {
    if (open && projectId) {
      fetchScan(false);
    }
  }, [open, projectId, fetchScan]);

  // Iter 212m-128 — Listen for the global `aurem:finding-fixed`
  // event fired by FixProgressDrawer when a real commit lands.
  // Drop the matching finding + decrement the summary counters
  // live so the user sees the bug count tick down without waiting
  // for a re-scan.  By-severity and by-vuln aggregates are
  // re-derived from the surviving rows.
  useEffect(() => {
    function onFixed(e) {
      const fid = e?.detail?.finding_id;
      if (!fid) return;
      setData((d) => {
        if (!d) return d;
        const before = d.findings || [];
        const after = before.filter((x) => (x.id || x.rule_id) !== fid);
        if (after.length === before.length) return d;
        const by_severity = {};
        const by_vuln     = {};
        for (const f of after) {
          by_severity[f.severity] = (by_severity[f.severity] || 0) + 1;
          if (f.vuln) by_vuln[f.vuln] = (by_vuln[f.vuln] || 0) + 1;
        }
        return {
          ...d,
          findings: after,
          summary: {
            ...(d.summary || {}),
            total:       after.length,
            by_severity,
            by_vuln,
          },
        };
      });
    }
    window.addEventListener("aurem:finding-fixed", onFixed);
    return () => window.removeEventListener("aurem:finding-fixed", onFixed);
  }, []);

  if (!open) return null;

  const findings = data?.findings || [];
  const grouped = SEV_ORDER.reduce((acc, sev) => {
    acc[sev] = findings.filter((f) => f.severity === sev);
    return acc;
  }, {});

  const summary = data?.summary || { total: 0, by_severity: {}, by_vuln: {} };
  const hasCritical = (summary.by_severity?.critical || 0) > 0;

  return (
    <>
      {/* Scrim */}
      <div
        data-testid="security-scan-scrim"
        onClick={onClose}
        style={{
          position: "fixed", inset: 0, zIndex: 1200,
          background: "rgba(6,8,13,0.55)",
          backdropFilter: "blur(4px)",
        }}
      />
      {/* Drawer */}
      <aside
        data-testid="security-scan-drawer"
        style={{
          position: "fixed", top: 0, right: 0, bottom: 0,
          width: "min(520px, 100%)", zIndex: 1201,
          background: "var(--bg, #0d1018)",
          borderLeft: "1px solid var(--border, rgba(255,255,255,0.08))",
          color: "var(--text, #e8ecf3)",
          display: "flex", flexDirection: "column",
          boxShadow: "-20px 0 60px rgba(0,0,0,0.55)",
          animation: "scanDrawerIn 220ms ease-out",
        }}
      >
        <style>{`
          @keyframes scanDrawerIn {
            from { transform: translateX(100%); opacity: 0.6; }
            to   { transform: translateX(0); opacity: 1; }
          }
        `}</style>

        {/* Header */}
        <header
          style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "16px 20px",
            borderBottom: "1px solid var(--border, rgba(255,255,255,0.06))",
            background: hasCritical
              ? "linear-gradient(90deg, rgba(239,68,68,0.10), transparent)"
              : "linear-gradient(90deg, rgba(34,197,94,0.06), transparent)",
          }}
        >
          {hasCritical
            ? <ShieldAlert size={20} color="#fca5a5" />
            : <ShieldCheck size={20} color="#86efac" />}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 600, letterSpacing: 0.2 }}>
              Security Scan
            </div>
            <div style={{ fontSize: 11, color: "var(--text-dim, #9aa3b2)", marginTop: 2 }}>
              {projectLabel || "Active project"}
            </div>
          </div>
          <button
            type="button"
            data-testid="security-scan-rescan"
            onClick={() => fetchScan(true)}
            disabled={loading}
            title="Re-scan now (bypass cache)"
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "6px 10px",
              fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
              background: "transparent",
              border: "1px solid var(--border, rgba(255,255,255,0.12))",
              borderRadius: 6, cursor: loading ? "wait" : "pointer",
              color: "var(--text-dim, #9aa3b2)",
            }}
          >
            {loading ? <Loader2 size={12} className="anim-spin" /> : <RefreshCw size={12} />}
            Re-scan
          </button>
          <button
            type="button"
            data-testid="security-scan-close"
            onClick={onClose}
            style={{
              padding: 6, background: "transparent",
              border: "1px solid var(--border, rgba(255,255,255,0.12))",
              borderRadius: 6, cursor: "pointer", color: "var(--text-dim, #9aa3b2)",
            }}
          >
            <X size={14} />
          </button>
        </header>

        {/* Iter 212m-66 — Vanguard 2.0 opt-in toggles. Two-round
            deep scan and Auto-PR are off by default to preserve the
            legacy fast path; a single click upgrades the scan to the
            full security-engineer co-pilot. Disabled while a scan
            is in flight so a user can't change the contract mid-run. */}
        <div
          data-testid="security-scan-options"
          style={{
            display: "flex", gap: 8, padding: "8px 16px",
            borderBottom: "1px solid var(--border, rgba(255,255,255,0.04))",
            background: "rgba(255,255,255,0.015)",
            fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
          }}
        >
          <label
            data-testid="security-scan-toggle-two-round"
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "5px 10px", borderRadius: 999,
              cursor: loading ? "not-allowed" : "pointer",
              border: `1px solid ${twoRound ? "rgba(56,189,248,0.55)" : "rgba(255,255,255,0.12)"}`,
              background: twoRound ? "rgba(56,189,248,0.10)" : "transparent",
              color: twoRound ? "#7dd3fc" : "var(--text-dim, #9aa3b2)",
              opacity: loading ? 0.5 : 1,
              transition: "all 120ms",
            }}
            title="Two-round Vanguard: surface sweep + deep re-scan + chain detection + AI remediation report"
          >
            <input
              type="checkbox"
              checked={twoRound}
              disabled={loading}
              onChange={(e) => setTwoRound(e.target.checked)}
              style={{ accentColor: "#38bdf8", margin: 0 }}
            />
            <Sparkles size={11} />
            Deep scan + AI report
          </label>
          <label
            data-testid="security-scan-toggle-auto-pr"
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "5px 10px", borderRadius: 999,
              cursor: (loading || !twoRound) ? "not-allowed" : "pointer",
              border: `1px solid ${autoPr ? "rgba(168,85,247,0.55)" : "rgba(255,255,255,0.12)"}`,
              background: autoPr ? "rgba(168,85,247,0.10)" : "transparent",
              color: autoPr ? "#d8b4fe" : "var(--text-dim, #9aa3b2)",
              opacity: (loading || !twoRound) ? 0.5 : 1,
              transition: "all 120ms",
            }}
            title={twoRound
              ? "Open a DRAFT GitHub PR with the remediation report (never force-merged)"
              : "Enable Deep scan first to open an auto-PR"}
          >
            <input
              type="checkbox"
              checked={autoPr}
              disabled={loading || !twoRound}
              onChange={(e) => setAutoPr(e.target.checked)}
              style={{ accentColor: "#a855f7", margin: 0 }}
            />
            <GitPullRequest size={11} />
            Auto open PR
          </label>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: "auto", padding: 16 }}>
          {/* Iter 212m-120 — CI Secret Scan (trufflehog) card.
              Sits ABOVE the in-process Vanguard findings so the
              user sees the GitHub Actions verdict first. Hidden
              when the repo has no owner/name yet. */}
          {repoOwner && repoName && (
            <div style={{ marginBottom: 12, marginLeft: -16, marginRight: -16 }}>
              <SecretScanCard
                repoOwner={repoOwner}
                repoName={repoName}
                variant="drawer"
                defaultExpanded={false}
              />
            </div>
          )}
          {loading && !data && (
            <div
              data-testid="security-scan-loading"
              style={{
                display: "flex", flexDirection: "column", alignItems: "center",
                justifyContent: "center", height: "100%", color: "var(--text-dim, #9aa3b2)",
                gap: 12, fontSize: 13,
              }}
            >
              <Loader2 size={28} className="anim-spin" />
              {twoRound
                ? "Deep two-round scan in progress… up to 30s"
                : "Scanning repository… this can take 10–20s"}
            </div>
          )}

          {error && !loading && (
            <div
              data-testid="security-scan-error"
              style={{
                padding: 14, borderRadius: 10,
                background: "rgba(239,68,68,0.08)",
                border: "1px solid rgba(239,68,68,0.4)",
                color: "#fca5a5", fontSize: 12.5, lineHeight: 1.5,
              }}
            >
              <strong style={{ color: "#fecaca" }}>Scan failed</strong><br />
              {error}
              <div style={{ marginTop: 10, color: "var(--text-dim, #9aa3b2)", fontSize: 11 }}>
                Tip: confirm the project has a connected GitHub repo + valid PAT.
              </div>
            </div>
          )}

          {data && !loading && (
            <>
              {(data.fixed_count || 0) > 0 && (
                <div data-testid="security-scan-fixed-count" style={{
                  marginBottom: 10, fontSize: 11.5, color: "#86efac",
                  fontFamily: "'JetBrains Mono', monospace",
                }}>
                  ✓ {data.fixed_count} previously fixed — excluded from results
                </div>
              )}
              {/* Iter 212m-121 — Bulk fix button for ALL findings.
                  Opens the cost-preview modal; founders see ⚡ FREE. */}
              {canBulk && (data.findings || []).length > 0 && (                <button
                  type="button"
                  data-testid="security-scan-bulk-fix"
                  onClick={() => setBulkOpen(true)}
                  style={{
                    marginBottom: 14, width: "100%",
                    padding: "10px 14px", borderRadius: 8,
                    background: "linear-gradient(135deg, #fb923c, #ea580c)",
                    border: "1px solid rgba(251,146,60,0.55)",
                    color: "#fff", cursor: "pointer",
                    fontSize: 13, fontWeight: 700,
                    fontFamily: "'JetBrains Mono', monospace",
                    letterSpacing: 0.3,
                    display: "inline-flex", alignItems: "center",
                    justifyContent: "center", gap: 8,
                    boxShadow: "0 4px 18px rgba(251,146,60,0.25)",
                  }}
                >
                  ⚡ Fix all {(data.findings || []).length} →
                </button>
              )}
              {/* Summary tiles.  Iter 212m-129: a fifth "Other"
                  tile renders ONLY when the total of the 4 known
                  severities is short of `findings.length` — this
                  guarantees the tile totals always equal the `Fix
                  all N →` button count, surfacing the gap instead
                  of hiding it. */}
              {(() => {
                const total4 = SEV_ORDER.reduce(
                  (n, s) => n + (summary.by_severity?.[s] || 0), 0,
                );
                const totalAll = (data.findings || []).length;
                const otherCount = Math.max(0, totalAll - total4);
                const tiles = otherCount > 0
                  ? [...SEV_ORDER, "other"]
                  : SEV_ORDER;
                return (
                  <div
                    data-testid="security-scan-summary"
                    style={{
                      display: "grid",
                      gridTemplateColumns: `repeat(${tiles.length}, 1fr)`,
                      gap: 8, marginBottom: 16,
                    }}
                  >
                    {tiles.map((sev) => {
                      const n = sev === "other"
                        ? otherCount
                        : (summary.by_severity?.[sev] || 0);
                      const c = SEV_COLORS[sev];
                      return (
                        <div
                          key={sev}
                          data-testid={`security-scan-tile-${sev}`}
                          title={sev === "other"
                            ? `${otherCount} findings without a critical/high/medium/low severity (info, unknown, or null). Included in 'Fix all'.`
                            : undefined}
                          style={{
                            padding: "10px 8px", borderRadius: 8,
                            background: c.bg, border: `1px solid ${c.border}`,
                            textAlign: "center",
                          }}
                        >
                          <div style={{ fontSize: 18, fontWeight: 700, color: c.fg }}>{n}</div>
                          <div style={{
                            fontSize: 10, color: c.fg, opacity: 0.85,
                            textTransform: "uppercase", letterSpacing: 0.6, marginTop: 2,
                          }}>
                            {sev}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                );
              })()}

              {/* Meta strip */}
              <div style={{
                display: "flex", justifyContent: "space-between",
                fontSize: 11, color: "var(--text-dim, #9aa3b2)",
                marginBottom: 12, fontFamily: "'JetBrains Mono', monospace",
                flexWrap: "wrap", gap: 6,
              }}>
                <span>
                  {data.scanned_files} files scanned
                  {data.scan_mode === "two_round" && (
                    <span style={{
                      marginLeft: 6, padding: "1px 6px",
                      borderRadius: 4, background: "rgba(56,189,248,0.16)",
                      color: "#7dd3fc", fontSize: 10,
                    }}>DEEP</span>
                  )}
                </span>
                {cachedAt && (
                  <span title="Cached result">
                    cached • {Math.round((Date.now() - cachedAt) / 1000)}s ago
                  </span>
                )}
                {data.truncated && (
                  <span style={{ color: "#fdba74" }}>showing top 500 findings</span>
                )}
              </div>

              {/* Iter 212m-66 — Two-round stats strip (R1 + R2 + chain). */}
              {data.two_round && (
                <div
                  data-testid="security-scan-two-round-stats"
                  style={{
                    display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap",
                    fontSize: 10.5, fontFamily: "'JetBrains Mono', monospace",
                  }}
                >
                  <span style={_chipStyle("rgba(56,189,248,0.42)", "#7dd3fc")}>
                    R1: {data.two_round.round1_count}
                  </span>
                  <span style={_chipStyle("rgba(56,189,248,0.42)", "#7dd3fc")}>
                    R2: {data.two_round.round2_count} ({data.two_round.files_round2} files)
                  </span>
                  {data.two_round.chain_count > 0 && (
                    <span style={_chipStyle("rgba(239,68,68,0.55)", "#fca5a5")}>
                      chains: {data.two_round.chain_count}
                    </span>
                  )}
                  {data.two_round.round2_skipped && (
                    <span style={_chipStyle("rgba(245,158,11,0.55)", "#fbbf24")}>
                      R2 skipped (budget)
                    </span>
                  )}
                  <span style={{ color: "var(--text-dim, #9aa3b2)" }}>
                    {data.two_round.elapsed_seconds}s
                  </span>
                </div>
              )}

              {/* Iter 212m-66 — Draft PR success banner. */}
              {data.pr_url && (
                <a
                  data-testid="security-scan-pr-banner"
                  href={data.pr_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{
                    display: "flex", alignItems: "center", gap: 10,
                    padding: "10px 12px", marginBottom: 12, borderRadius: 8,
                    background: "rgba(168,85,247,0.10)",
                    border: "1px solid rgba(168,85,247,0.45)",
                    color: "#d8b4fe", textDecoration: "none",
                    fontSize: 12, lineHeight: 1.4,
                  }}
                >
                  <GitPullRequest size={16} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600 }}>Draft PR opened</div>
                    <div style={{
                      fontSize: 10.5, color: "#c4b5fd", opacity: 0.85,
                      overflow: "hidden", textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}>
                      {data.pr_url}
                    </div>
                  </div>
                  <ExternalLink size={13} />
                </a>
              )}
              {data.pr_error && (
                <div
                  data-testid="security-scan-pr-error"
                  style={{
                    padding: "8px 12px", marginBottom: 12, borderRadius: 8,
                    background: "rgba(245,158,11,0.08)",
                    border: "1px solid rgba(245,158,11,0.40)",
                    color: "#fbbf24", fontSize: 11, lineHeight: 1.4,
                  }}
                >
                  <strong>PR not opened:</strong> {data.pr_error}
                </div>
              )}

              {/* Iter 212m-66 — AI remediation report (collapsible). */}
              {data.remediation_report && (
                <div
                  data-testid="security-scan-ai-report"
                  style={{
                    marginBottom: 16, borderRadius: 10,
                    background: "rgba(56,189,248,0.06)",
                    border: "1px solid rgba(56,189,248,0.32)",
                    overflow: "hidden",
                  }}
                >
                  <button
                    type="button"
                    data-testid="security-scan-ai-report-toggle"
                    onClick={() => setReportOpen((o) => !o)}
                    style={{
                      width: "100%", display: "flex", alignItems: "center",
                      gap: 8, padding: "10px 12px", background: "transparent",
                      border: "none", cursor: "pointer", textAlign: "left",
                      color: "#7dd3fc", fontSize: 12, fontWeight: 600,
                    }}
                  >
                    {reportOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    <Sparkles size={13} />
                    AI Remediation Report
                    <span style={{
                      marginLeft: "auto", fontSize: 10.5,
                      color: "var(--text-dim, #9aa3b2)",
                      fontFamily: "'JetBrains Mono', monospace",
                    }}>
                      risk {data.remediation_report.risk_score}/100
                      {data.report_status && data.report_status !== "ok" && (
                        <span style={{ marginLeft: 6, color: "#fbbf24" }}>
                          · {data.report_status}
                        </span>
                      )}
                    </span>
                  </button>
                  {reportOpen && (
                    <div style={{ padding: "0 14px 14px 14px" }}>
                      <div style={{
                        fontSize: 11.5, color: "var(--text, #e8ecf3)",
                        marginBottom: 10, lineHeight: 1.5,
                      }}>
                        {data.remediation_report.summary}
                      </div>
                      {(data.remediation_report.findings || []).map((rf, i) => (
                        <div
                          key={`rf-${i}`}
                          data-testid="security-scan-ai-fix"
                          style={{
                            padding: 10, marginBottom: 8, borderRadius: 7,
                            background: "rgba(0,0,0,0.28)",
                            border: "1px solid rgba(255,255,255,0.08)",
                          }}
                        >
                          <div style={{
                            display: "flex", alignItems: "center", gap: 6,
                            fontSize: 11, marginBottom: 4,
                            fontFamily: "'JetBrains Mono', monospace",
                          }}>
                            <span style={{
                              padding: "1px 6px", borderRadius: 4,
                              background: SEV_COLORS[(rf.severity || "low")
                                .toLowerCase()]?.bg || "rgba(125,211,252,0.10)",
                              color: SEV_COLORS[(rf.severity || "low")
                                .toLowerCase()]?.fg || "#bae6fd",
                              fontSize: 9.5, textTransform: "uppercase",
                              letterSpacing: 0.4,
                            }}>{rf.severity}</span>
                            <code style={{
                              color: "#cbd5e1", fontSize: 10.5,
                              wordBreak: "break-all",
                            }}>{rf.file}:{rf.line}</code>
                            {rf.pr_ready && (
                              <span title="Mechanical fix — safe to merge"
                                    style={{
                                      marginLeft: "auto", fontSize: 9.5,
                                      color: "#86efac", padding: "1px 6px",
                                      borderRadius: 4,
                                      background: "rgba(34,197,94,0.10)",
                                      border: "1px solid rgba(34,197,94,0.40)",
                                    }}>
                                PR-ready
                              </span>
                            )}
                          </div>
                          <div style={{
                            fontSize: 11.5, color: "var(--text, #e8ecf3)",
                            marginBottom: 6, lineHeight: 1.5,
                          }}>
                            {rf.what_is_wrong}
                          </div>
                          {rf.fix && (
                            <pre style={{
                              margin: 0, padding: "6px 8px",
                              background: "rgba(0,0,0,0.42)",
                              borderRadius: 5, fontSize: 10.5,
                              fontFamily: "'JetBrains Mono', monospace",
                              color: "#86efac", overflowX: "auto",
                              whiteSpace: "pre-wrap", wordBreak: "break-all",
                            }}>
                              {rf.fix}
                            </pre>
                          )}
                        </div>
                      ))}
                      {!(data.remediation_report.findings || []).length && (
                        <div style={{
                          padding: "8px 4px", fontSize: 11,
                          color: "var(--text-dim, #9aa3b2)",
                        }}>
                          AI report unavailable — see raw findings below.
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {summary.total === 0 ? (
                <div
                  data-testid="security-scan-empty"
                  style={{
                    padding: "32px 16px", textAlign: "center",
                    color: "var(--text-dim, #9aa3b2)", fontSize: 13,
                  }}
                >
                  <ShieldCheck size={36} color="#86efac" style={{ margin: "0 auto 10px" }} />
                  <div style={{ color: "var(--text, #e8ecf3)", fontWeight: 600, marginBottom: 4 }}>
                    No vulnerabilities detected
                  </div>
                  <div>Your repo passes the static rule library.</div>
                </div>
              ) : (
                SEV_ORDER.map((sev) => {
                  const list = grouped[sev];
                  if (!list.length) return null;
                  const c = SEV_COLORS[sev];
                  return (
                    <section
                      key={sev}
                      data-testid={`security-scan-section-${sev}`}
                      style={{ marginBottom: 18 }}
                    >
                      <h3 style={{
                        fontSize: 11, textTransform: "uppercase",
                        letterSpacing: 0.8, color: c.fg, marginBottom: 8,
                        fontFamily: "'JetBrains Mono', monospace",
                      }}>
                        {sev} ({list.length})
                      </h3>
                      <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                        {list.map((f, i) => {
                          const k       = findingKey(f);
                          const fixedAt = fixedKeys[k];
                          const isFixing = fixingKey === k;
                          return (
                          <li
                            key={`${f.file}:${f.line}:${f.rule_id}:${i}`}
                            data-testid={`security-scan-finding-${sev}`}
                            data-fix-status={fixedAt ? "fixed" : "open"}
                            style={{
                              padding: "10px 12px", marginBottom: 6,
                              borderRadius: 8,
                              background: c.bg,
                              border: `1px solid ${c.border}`,
                              opacity: fixedAt ? 0.55 : 1,
                              transition: "opacity 200ms ease",
                            }}
                          >
                            <div style={{
                              display: "flex", alignItems: "center", gap: 8,
                              fontSize: 12, marginBottom: 4,
                            }}>
                              <FileWarning size={12} color={c.fg} />
                              <code style={{
                                fontFamily: "'JetBrains Mono', monospace",
                                color: c.fg, fontSize: 11.5,
                                wordBreak: "break-all",
                              }}>
                                {f.file}:{f.line}
                              </code>
                              <span style={{
                                marginLeft: "auto", fontSize: 10,
                                color: "var(--text-dim, #9aa3b2)",
                                fontFamily: "'JetBrains Mono', monospace",
                              }}>
                                {f.vuln}
                              </span>
                            </div>
                            <div style={{
                              fontSize: 12, color: "var(--text, #e8ecf3)",
                              lineHeight: 1.5, marginBottom: 6,
                            }}>
                              {f.desc}
                            </div>
                            <pre style={{
                              margin: 0, padding: "6px 8px",
                              background: "rgba(0,0,0,0.32)",
                              borderRadius: 5,
                              fontSize: 10.5,
                              fontFamily: "'JetBrains Mono', monospace",
                              color: "var(--text-dim, #c2c9d6)",
                              overflowX: "auto",
                              whiteSpace: "pre-wrap",
                              wordBreak: "break-all",
                            }}>
                              {f.snippet}
                            </pre>
                            {/* Iter 212m-114 — REAL Fix button */}
                            <div style={{
                              display: "flex", alignItems: "center",
                              gap: 8, marginTop: 8, justifyContent: "flex-end",
                            }}>
                              {fixedAt ? (
                                <>
                                  <CheckCircle2 size={12} color="#22c55e" />
                                  <span style={{
                                    fontSize: 10.5, color: "#86efac",
                                    fontFamily: "'JetBrains Mono', monospace",
                                  }}>
                                    Fixed · commit{" "}
                                    {fixedAt.html_url ? (
                                      <a
                                        href={fixedAt.html_url}
                                        target="_blank" rel="noreferrer"
                                        style={{ color: "#86efac", textDecoration: "underline" }}
                                        data-testid="finding-fix-commit-link"
                                      >
                                        {fixedAt.commit_sha}
                                      </a>
                                    ) : (
                                      <code>{fixedAt.commit_sha}</code>
                                    )}
                                  </span>
                                </>
                              ) : canFix ? (
                                <button
                                  type="button"
                                  onClick={() => handleApplyFix(f)}
                                  disabled={isFixing}
                                  data-testid="finding-fix-btn"
                                  data-rule-id={f.rule_id}
                                  style={{
                                    display: "inline-flex", alignItems: "center", gap: 5,
                                    padding: "5px 10px",
                                    background: isFixing
                                      ? "rgba(255,255,255,0.05)"
                                      : "linear-gradient(135deg, #FF6608, #ff8a3d)",
                                    color: isFixing ? "#9aa3b2" : "#0a0a0a",
                                    border: "1px solid transparent",
                                    borderRadius: 6,
                                    fontSize: 11, fontWeight: 700,
                                    fontFamily: "'JetBrains Mono', monospace",
                                    cursor: isFixing ? "wait" : "pointer",
                                    letterSpacing: 0.3,
                                  }}
                                >
                                  {isFixing ? (
                                    <>
                                      <Loader2 size={11} className="aurem-spin" />
                                      Fixing…
                                    </>
                                  ) : (
                                    <>
                                      <Wrench size={11} />
                                      Fix · 1 task
                                    </>
                                  )}
                                </button>
                              ) : null}
                            </div>
                          </li>
                          );
                        })}
                      </ul>
                    </section>
                  );
                })
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <footer style={{
          padding: "10px 16px",
          borderTop: "1px solid var(--border, rgba(255,255,255,0.06))",
          fontSize: 10.5, color: "var(--text-dim, #9aa3b2)",
          fontFamily: "'JetBrains Mono', monospace",
          textAlign: "center",
        }}>
          Real-time scan · per-finding Fix button · founder = free{twoRound && " · deep mode"}{autoPr && " · auto-PR on"}
        </footer>
      </aside>
      {/* Iter 212m-121 — Bulk fix confirm modal. Mounted on the
          drawer so it overlays correctly when the drawer is open. */}
      <BulkFixConfirmModal
        open={bulkOpen}
        onClose={() => setBulkOpen(false)}
        projectId={projectId}
        findings={(data?.findings || []).map((f) => ({
          ...f, category: "vanguard",
        }))}
        category="Vanguard"
        tool="vanguard-scan"
      />
    </>
  );
}
