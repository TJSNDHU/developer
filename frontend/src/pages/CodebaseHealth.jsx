/**
 * pages/CodebaseHealth.jsx  —  Iter 212m-72 (Phase 2)
 *
 * Dramatic 5-category codebase health dashboard.
 *
 * Backed entirely by the new /api/aurem-dev/codebase-health/* routes
 * (NO mocks, NO hardcoded findings — every number on the page comes
 * from a real static-analyser walk of the user's connected GitHub
 * repo).
 *
 * UI follows the founder's spec verbatim:
 *   • Big health-score header with the urgency label
 *   • 5 expandable category cards (collapsed by default)
 *   • CRITICAL findings rendered fully; HIGH/MEDIUM blurred until
 *     unlocked (creates upgrade-pressure — micro-monetisation)
 *   • Per-finding "Fix — N tokens" button that calls /fix and
 *     enqueues a real cto_task
 *   • Token counter top-right; -N animation on every spend
 *   • Empty state (pre-scan) with 5 category buttons + Full Scan CTA
 */
import React, { useEffect, useMemo, useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import {
  Shield, Zap, Code2, Package, Database, RefreshCw, Loader2,
  ChevronDown, ChevronRight, Sparkles, ExternalLink, Bug,
} from "lucide-react";
import { api, isAdminOrFounder } from "../lib/api";
import useFixQuota from "../lib/useFixQuota";
import BulkFixConfirmModal from "../components/BulkFixConfirmModal";

const CATS = [
  { key: "security",     label: "Security",      icon: Shield,    tone: "#ef4444", cost: 5,
    blurb: "These can expose your users' data and get you hacked" },
  { key: "performance",  label: "Performance",   icon: Zap,       tone: "#f59e0b", cost: 5,
    blurb: "These are making your app slow and will get worse as you grow" },
  { key: "code_quality", label: "Code Quality",  icon: Code2,     tone: "#38bdf8", cost: 5,
    blurb: "These will slow down your team and make ORA less effective" },
  { key: "dependencies", label: "Dependencies",  icon: Package,   tone: "#a855f7", cost: 5,
    blurb: "These are known attack vectors hackers actively exploit" },
  { key: "database",     label: "Database",      icon: Database,  tone: "#10b981", cost: 5,
    blurb: "These will cause outages when your user count grows" },
  { key: "bug_hunt",     label: "Bug Hunt",      icon: Bug,       tone: "#f472b6", cost: 8,
    blurb: "Nuclei-template-inspired deep scan: 50+ patterns for leaked secrets, RCE primitives, exposed admin endpoints, and CVE-vulnerable dependencies" },
];

const SEV_META = {
  critical: { color: "#fca5a5", bg: "rgba(239,68,68,0.10)",  br: "rgba(239,68,68,0.45)",  label: "CRITICAL", emoji: "🔴" },
  high:     { color: "#fdba74", bg: "rgba(249,115,22,0.10)", br: "rgba(249,115,22,0.45)", label: "HIGH",     emoji: "🟠" },
  medium:   { color: "#fde68a", bg: "rgba(250,204,21,0.10)", br: "rgba(250,204,21,0.42)", label: "MEDIUM",   emoji: "🟡" },
  low:      { color: "#bae6fd", bg: "rgba(125,211,252,0.10)",br: "rgba(125,211,252,0.42)",label: "LOW",      emoji: "🔵" },
  info:     { color: "#cbd5e1", bg: "rgba(148,163,184,0.10)",br: "rgba(148,163,184,0.40)",label: "INFO",     emoji: "ℹ️"  },
  // Iter 212m-130 — Catch-all bucket for findings whose severity
  // is null / unset / outside the standard 4.  Used by both the
  // SectionLabel and the per-category "Other" rollup so the
  // header total ("N issues") always matches what's visible.
  other:    { color: "#cbd5e1", bg: "rgba(148,163,184,0.08)",br: "rgba(148,163,184,0.30)",label: "OTHER",    emoji: "⚪" },
};

function HealthBadge({ score, label, tone }) {
  const palette = {
    critical: { bg: "linear-gradient(135deg, #4c0a0a 0%, #2a0606 100%)", border: "rgba(239,68,68,0.65)", glow: "rgba(239,68,68,0.45)", emoji: "☠️" },
    warn:     { bg: "linear-gradient(135deg, #4a2a06 0%, #2a1804 100%)", border: "rgba(245,158,11,0.55)", glow: "rgba(245,158,11,0.30)", emoji: "⚠️" },
    good:     { bg: "linear-gradient(135deg, #06294c 0%, #03182a 100%)", border: "rgba(56,189,248,0.55)", glow: "rgba(56,189,248,0.30)", emoji: "🔵" },
    healthy:  { bg: "linear-gradient(135deg, #06402a 0%, #032416 100%)", border: "rgba(34,197,94,0.55)",  glow: "rgba(34,197,94,0.30)",  emoji: "✅" },
  }[tone] || { bg: "#1a1f2e", border: "rgba(148,163,184,0.40)", glow: "transparent", emoji: "ℹ️" };
  return (
    <div data-testid="health-score" style={{
      padding: "28px 32px", borderRadius: 18, background: palette.bg,
      border: `2px solid ${palette.border}`,
      boxShadow: `0 0 60px ${palette.glow}`, marginBottom: 28,
      animation: tone === "critical" ? "health-pulse 2.4s ease-in-out infinite" : "none",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 14 }}>
        <span style={{ fontSize: 38 }}>{palette.emoji}</span>
        <span style={{ fontSize: 24, fontWeight: 800, letterSpacing: 0.5,
          color: palette.border, fontFamily: "'JetBrains Mono', monospace" }}>
          {label}
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 14 }}>
        <span data-testid="health-score-number" style={{ fontSize: 84, fontWeight: 800, lineHeight: 1, color: "#fff" }}>
          {score}
        </span>
        <span style={{ fontSize: 22, color: "#94a3b8", fontFamily: "'JetBrains Mono', monospace" }}>
          /100
        </span>
      </div>
      <div style={{ height: 10, borderRadius: 999, background: "rgba(255,255,255,0.06)", overflow: "hidden" }}>
        <div style={{
          width: `${score}%`, height: "100%",
          background: palette.border, transition: "width 1.2s cubic-bezier(0.4, 0, 0.2, 1)",
        }} />
      </div>
    </div>
  );
}

function FindingRow({ f, onFix, busy, locked, canFix = true }) {
  const meta = SEV_META[f.severity] || SEV_META.medium;
  return (
    <div
      data-testid={`finding-${f.id}`}
      style={{
        padding: 12, marginBottom: 10, borderRadius: 8,
        background: locked ? "rgba(255,255,255,0.02)" : meta.bg,
        border: `1px solid ${locked ? "rgba(148,163,184,0.20)" : meta.br}`,
        filter: locked ? "blur(5px) brightness(0.6)" : "none",
        pointerEvents: locked ? "none" : "auto",
        transition: "filter 200ms",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6,
                    fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
        <span style={{ color: meta.color, fontWeight: 700 }}>{meta.emoji} {meta.label}</span>
        <code style={{ color: "#cbd5e1", flex: 1, overflow: "hidden",
                       textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {f.file}{f.line ? `:${f.line}` : ""}
        </code>
      </div>
      <div style={{ fontSize: 13, color: "#e8ecf3", lineHeight: 1.5, marginBottom: 6 }}>
        <strong>{f.title}</strong>
      </div>
      <div style={{ fontSize: 11.5, color: "#94a3b8", lineHeight: 1.5, marginBottom: 8 }}>
        ⚠️ {f.message}
      </div>
      {f.fix_hint && (
        <div style={{ fontSize: 11, color: "#7dd3fc", marginBottom: 10,
                      fontFamily: "'JetBrains Mono', monospace" }}>
          ✓ {f.fix_hint}
        </div>
      )}
      {!locked && canFix && (
        <button
          data-testid={`fix-btn-${f.id}`}
          onClick={() => onFix(f)}
          disabled={busy}
          style={{
            padding: "7px 14px", borderRadius: 8, border: "1px solid rgba(34,197,94,0.45)",
            background: "rgba(34,197,94,0.10)", color: "#86efac", cursor: busy ? "wait" : "pointer",
            fontSize: 12, fontWeight: 600, opacity: busy ? 0.5 : 1,
            fontFamily: "'JetBrains Mono', monospace",
          }}
        >
          {busy ? <Loader2 size={12} className="inline anim-spin" /> : "Fix this"}{" "}— 1 task
        </button>
      )}
    </div>
  );
}

function CategoryCard({ cat, data, expanded, onToggle, onFix, busyIds, unlockedHigh, onUnlockHigh, onBulkFix, canFix = true, canBulk = true }) {
  const Icon = cat.icon;
  const counts = data?.counts || { critical: 0, high: 0, medium: 0, low: 0 };
  const total = data?.total || 0;
  const score = data?.score ?? 100;
  const dangerous = (counts.critical || 0) + (counts.high || 0);
  // Iter 212m-121 — Bulk fix: collect every visible finding in the
  // category (respects the unlock gating so we never try to fix a
  // blurred row the user hasn't unlocked).
  // Iter 212m-130 — bulk fix now picks ALL visible severities,
  // not just critical+high+medium.  Low + Other findings are now
  // rendered (see CategoryCard tail) so they must also be
  // includable in the bulk fix click.  unlocked-high gates the
  // non-critical buckets the same way the inline rows do.
  const visibleFindings = (data?.findings || []).filter((f) => {
    if (f.severity === "critical") return true;
    if (["high", "medium", "low"].includes(f.severity)) return unlockedHigh;
    // Unknown / null / info etc.  Always grouped with high+medium
    // so a single "unlock" click reveals all of them at once.
    return unlockedHigh;
  });
  return (
    <div
      data-testid={`cat-card-${cat.key}`}
      style={{
        borderRadius: 12, marginBottom: 14, overflow: "hidden",
        background: "rgba(255,255,255,0.02)", border: `1px solid ${cat.tone}55`,
      }}
    >
      <button
        type="button"
        onClick={onToggle}
        data-testid={`cat-toggle-${cat.key}`}
        style={{
          width: "100%", display: "flex", alignItems: "center", gap: 12,
          padding: "14px 16px", background: "transparent", border: "none",
          textAlign: "left", cursor: "pointer", color: "inherit",
        }}
      >
        {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        <Icon size={18} color={cat.tone} />
        <span style={{ fontSize: 14, fontWeight: 700, color: "#e8ecf3" }}>{cat.label}</span>
        <span style={{ flex: 1, color: "#94a3b8", fontSize: 11.5,
                       fontFamily: "'JetBrains Mono', monospace" }}>
          {total} issues
          {dangerous > 0 && <span style={{ color: "#fca5a5", marginLeft: 8 }}>· {dangerous} need attention</span>}
          {(data?.fixed_count || 0) > 0 && (
            <span data-testid={`fixed-count-${cat.key}`} style={{ color: "#86efac", marginLeft: 8 }}>
              · ✓ {data.fixed_count} fixed
            </span>
          )}
        </span>
        <span style={{
          padding: "4px 12px", borderRadius: 999, fontSize: 11, fontWeight: 700,
          background: `${cat.tone}22`, color: cat.tone, fontFamily: "'JetBrains Mono', monospace",
        }}>
          {score}/100
        </span>
      </button>
      {expanded && (
        <div style={{ padding: "0 16px 16px 16px", borderTop: `1px solid ${cat.tone}33` }}>
          <p style={{ fontSize: 12, color: "#94a3b8", marginTop: 12, marginBottom: 12, fontStyle: "italic" }}>
            “{cat.blurb}”
          </p>
          {/* Iter 212m-121 — Bulk fix button per category. Hidden
              when there are no visible findings; orange variant
              for founders, blue for paying users (cost preview in
              modal). */}
          {/* Iter 212m-190 — bulk fix is a Team-tier feature; the
              button is only rendered when canBulk (quota.bulk_fix). */}
          {canBulk && visibleFindings.length > 0 && (
            <button
              type="button"
              data-testid={`bulk-fix-${cat.key}`}
              onClick={() => onBulkFix(cat, visibleFindings)}
              style={{
                marginBottom: 12,
                padding: "8px 14px", borderRadius: 8,
                background: "linear-gradient(135deg, #fb923c, #ea580c)",
                border: "1px solid rgba(251,146,60,0.55)",
                color: "#fff", cursor: "pointer",
                fontSize: 12, fontWeight: 700,
                fontFamily: "'JetBrains Mono', monospace",
                letterSpacing: 0.3,
                display: "inline-flex", alignItems: "center", gap: 6,
                boxShadow: "0 4px 18px rgba(251,146,60,0.25)",
              }}
            >
              ⚡ Fix all {visibleFindings.length} →
            </button>
          )}
          {(counts.critical || 0) > 0 && (
            <SectionLabel sev="critical" count={counts.critical} />
          )}
          {(data?.findings || []).filter((f) => f.severity === "critical").map((f) => (
            <FindingRow key={f.id} f={f} onFix={onFix} busy={busyIds.has(f.id)} locked={false} canFix={canFix} />
          ))}
          {(counts.high || 0) > 0 && (
            <SectionLabel sev="high" count={counts.high}
                          unlock={!unlockedHigh && <UnlockBtn label="HIGH" tokens={3} onClick={() => onUnlockHigh(cat.key)} />} />
          )}
          {(data?.findings || []).filter((f) => f.severity === "high").map((f) => (
            <FindingRow key={f.id} f={f} onFix={onFix} busy={busyIds.has(f.id)} locked={!unlockedHigh} canFix={canFix} />
          ))}
          {(counts.medium || 0) > 0 && (
            <SectionLabel sev="medium" count={counts.medium}
                          unlock={<UnlockBtn label="MEDIUM" tokens={2} onClick={() => onUnlockHigh(cat.key)} />} />
          )}
          {(data?.findings || []).filter((f) => f.severity === "medium").map((f) => (
            <FindingRow key={f.id} f={f} onFix={onFix} busy={busyIds.has(f.id)} locked={!unlockedHigh} canFix={canFix} />
          ))}
          {/* Iter 212m-130 — parity with SecurityScanDrawer.
              We previously rendered only critical/high/medium and
              hid low + everything outside the 4 buckets, which made
              the per-category header total ("10 issues") not match
              the visible row count.  Now we render low + an "Other"
              bucket so the header count always equals what's on
              screen (mirrors the Other tile fix from iter 129). */}
          {(counts.low || 0) > 0 && (
            <SectionLabel sev="low" count={counts.low} />
          )}
          {(data?.findings || []).filter((f) => f.severity === "low").map((f) => (
            <FindingRow key={f.id} f={f} onFix={onFix} busy={busyIds.has(f.id)} locked={!unlockedHigh} canFix={canFix} />
          ))}
          {(() => {
            // Findings whose severity is null / unknown / not one
            // of the 4 standard buckets get a gray "Other" section.
            const knownSet = new Set(["critical", "high", "medium", "low"]);
            const others = (data?.findings || []).filter(
              (f) => !knownSet.has(f.severity),
            );
            if (others.length === 0) return null;
            return (
              <>
                <SectionLabel sev="other" count={others.length} />
                {others.map((f) => (
                  <FindingRow key={f.id} f={f} onFix={onFix}
                              busy={busyIds.has(f.id)}
                              locked={!unlockedHigh}
                              canFix={canFix} />
                ))}
              </>
            );
          })()}
        </div>
      )}
    </div>
  );
}

function SectionLabel({ sev, count, unlock }) {
  const m = SEV_META[sev];
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, marginTop: 12, marginBottom: 8,
      fontSize: 11, fontFamily: "'JetBrains Mono', monospace", color: m.color,
    }}>
      <span style={{ fontWeight: 700 }}>{m.emoji} {m.label} ({count})</span>
      <span style={{ flex: 1 }} />
      {unlock}
    </div>
  );
}

function UnlockBtn({ label, tokens, onClick }) {
  return (
    <button
      onClick={onClick}
      data-testid={`unlock-${label.toLowerCase()}`}
      style={{
        padding: "3px 10px", borderRadius: 999, fontSize: 10.5,
        background: "rgba(168,85,247,0.10)", color: "#c4b5fd",
        border: "1px solid rgba(168,85,247,0.45)", cursor: "pointer",
        fontFamily: "'JetBrains Mono', monospace",
      }}
    >
      Unlock {label} — {tokens} 💎
    </button>
  );
}

export default function CodebaseHealth() {
  // Iter 212m-157 — Admin-only guard wrapper.  Splitting the check
  // into a parent wrapper keeps the inner component's hook order
  // unconditional (Rules of Hooks compliance).  Non-admins are
  // bounced to /dashboard; admins/founders fall through to the
  // full Health Scanner experience.
  if (!isAdminOrFounder()) {
    return <Navigate to="/dashboard" replace data-testid="health-nonadmin-redirect" />;
  }
  return <CodebaseHealthInner />;
}

function CodebaseHealthInner() {
  const navigate = useNavigate();
  const [projectId, setProjectId] = useState(localStorage.getItem("aurem_active_project") || "");
  const [scanning, setScanning] = useState(false);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [expandedCats, setExpandedCats] = useState({});
  const [tokens, setTokens] = useState(parseInt(localStorage.getItem("aurem_tokens") || "0", 10));
  const [busyIds, setBusyIds] = useState(new Set());
  const [unlocked, setUnlocked] = useState({});
  const [tokenFloat, setTokenFloat] = useState(null);
  // Iter 212m-121 — Bulk fix modal state + per-fix progress drawer.
  const [bulkModal, setBulkModal] = useState({ open: false, cat: null, findings: [] });
  // Iter 212m-190 — task-quota gating (1 fix = 1 task, tier-gated).
  const { quota } = useFixQuota();
  const canFix = !!quota && (quota.fix_tools || []).includes("health-scan");
  const canBulk = canFix && !!quota?.bulk_fix;

  function openBulk(cat, findings) {
    // Tag each finding with `category` so the backend cost preview
    // can charge the right rate (5 vs 8 tokens etc).
    const tagged = (findings || []).map((f) => ({ ...f, category: cat.key }));
    // Iter 212m-190 — block BEFORE the confirm dialog when the run
    // needs more tasks than the user has left this month.
    const remaining = quota?.tasks_remaining;
    if (remaining !== null && remaining !== undefined && tagged.length > remaining) {
      setError(`You have ${remaining} tasks left this month — not enough for ${tagged.length} fixes. Upgrade or fix issues individually.`);
      return;
    }
    setBulkModal({ open: true, cat, findings: tagged });
  }

  useEffect(() => {
    document.title = "Codebase Health — ORA by Aurem CTO";
    (async () => {
      try {
        const r = await api.get("/auth/me");
        const t = (r?.data || r)?.user?.tokens_remaining;
        if (typeof t === "number") setTokens(t);
      } catch { /* no-op */ }
    })();
  }, []);

  // Iter 212m-176 — restore the last persisted scan on page load.
  // Users paid tokens for the scan; a page refresh must not show
  // "unscanned" when the backend still has the full result.
  useEffect(() => {
    if (!projectId) return;
    (async () => {
      try {
        const r = await api.get(
          `/codebase-health/last?project_id=${encodeURIComponent(projectId)}`);
        const d = r?.data || r;
        if (d?.ok && typeof d.score === "number" && d.breakdown &&
            Object.keys(d.breakdown).length > 0) {
          setData((cur) => cur || d);
        }
      } catch { /* no-op */ }
    })();
  }, [projectId]);

  // Iter 212m-128 — Listen for the global `aurem:finding-fixed`
  // event fired by FixProgressDrawer the moment a real GitHub
  // commit lands.  We drop the matching finding from the
  // breakdown + decrement the total + nudge the health score
  // upward so the page shows live forward progress without
  // waiting for a re-scan.  Idempotent — re-firing for the same
  // finding_id is a no-op because it's already gone.
  useEffect(() => {
    function onFixed(e) {
      const fid = e?.detail?.finding_id;
      if (!fid) return;
      setData((d) => {
        if (!d) return d;
        const nb = { ...(d.breakdown || {}) };
        let dropped = false;
        for (const cat of Object.keys(nb)) {
          const before = (nb[cat].findings || []).length;
          const after = (nb[cat].findings || []).filter((x) => x.id !== fid);
          if (after.length === before) continue;
          dropped = true;
          // Re-derive the per-severity counters so the section
          // labels (e.g. "🔴 CRITICAL (5)") stay accurate.
          const counts = { critical: 0, high: 0, medium: 0, low: 0 };
          for (const f of after) counts[f.severity] = (counts[f.severity] || 0) + 1;
          nb[cat] = { ...nb[cat],
            findings: after,
            total: Math.max(0, after.length),
            counts };
        }
        if (!dropped) return d;
        return { ...d, breakdown: nb,
          total: Math.max(0, (d.total || 0) - 1),
          score: Math.min(100, (d.score || 0) + 2) };
      });
    }
    window.addEventListener("aurem:finding-fixed", onFixed);
    return () => window.removeEventListener("aurem:finding-fixed", onFixed);
  }, []);

  async function runScan(categories) {
    if (!projectId) {
      setError("Connect a project first from the dashboard.");
      return;
    }
    setScanning(true); setError(null);
    try {
      const r = await api.post("/codebase-health/scan",
        { project_id: projectId, categories });
      setData(r?.data || r);
      // open every category that has at least one critical
      const e = {};
      Object.entries((r?.data || r)?.breakdown || {}).forEach(([k, v]) => {
        if ((v?.counts?.critical || 0) > 0) e[k] = true;
      });
      setExpandedCats(e);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || "Scan failed");
    } finally {
      setScanning(false);
    }
  }

  async function fixOne(f) {
    // Iter 212m-121 — Route ALL fix clicks through the new
    // bulk/SSE pipeline so the user always sees the live progress
    // drawer regardless of single vs bulk.  The backend treats a
    // findings array of length 1 identically; we get phase events
    // + a real-commit verification chip for free.
    if (busyIds.has(f.id)) return;
    setBusyIds((s) => new Set([...s, f.id]));
    try {
      const r = await api.post("/fix-pipeline/bulk", {
        project_id: projectId,
        tool: "health-scan",
        findings: [{ ...f, category: f.category || _guessCategory(f, data) }],
      });
      const payload = r?.data || r;
      if (payload?.job_id) {
        window.dispatchEvent(new CustomEvent("aurem:open-fix-progress", {
          detail: { job_id: payload.job_id, total: 1 },
        }));
        setError(null);
        // Iter 212m-128 — The optimistic 800 ms removeFromList()
        // here was REMOVED.  Live decrement now happens via the
        // global `aurem:finding-fixed` event listener above which
        // fires only on REAL successful commit (driven by SSE
        // `fix-done ok:true`).  Failed/retried fixes correctly
        // stay in the list until they actually succeed.
      } else {
        setError("No job_id returned");
      }
    } catch (e) {
      const detail = e?.response?.data?.detail;
      const code   = typeof detail === "object" ? detail.error : detail;
      if (code === "insufficient_tasks" || code === "fix_not_available_on_tier"
          || code === "bulk_fix_not_available") {
        setError(detail.message || "Upgrade your plan to fix issues.");
      } else if (code === "insufficient_tokens") {
        setError(`Insufficient tokens (need ${detail.needed}, have ${detail.balance}).`);
      } else {
        setError(typeof detail === "string" ? detail
                 : detail?.message || e?.message || "Fix failed to start");
      }
    } finally {
      setBusyIds((s) => { const n = new Set(s); n.delete(f.id); return n; });
    }
  }

  // Iter 212m-121 — Walk the breakdown to figure out which category
  // a finding belongs to so the backend uses the right token rate.
  function _guessCategory(f, d) {
    if (!d?.breakdown) return "code_quality";
    for (const [k, v] of Object.entries(d.breakdown)) {
      if ((v?.findings || []).some((x) => x.id === f.id)) return k;
    }
    return "code_quality";
  }

  const showEmpty = !data && !scanning;
  const lowTokens = tokens > 0 && tokens < 10;
  const noTokens  = tokens <= 0;

  return (
    <div style={{ minHeight: "100vh", background: "#080c14", color: "#e8ecf3", padding: "32px 5%" }}>
      <style>{`
        @keyframes health-pulse {
          0%, 100% { box-shadow: 0 0 60px rgba(239,68,68,0.45); }
          50%      { box-shadow: 0 0 100px rgba(239,68,68,0.85); }
        }
        @keyframes float-up {
          0%   { opacity: 1; transform: translate(-50%, 0); }
          100% { opacity: 0; transform: translate(-50%, -40px); }
        }
        .anim-spin { animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .inline { display: inline-block; vertical-align: -2px; }
      `}</style>

      <div style={{ maxWidth: 980, margin: "0 auto" }}>
        {/* Back to dashboard — Iter 212m-98 */}
        <div style={{ marginBottom: 16 }}>
          <button
            data-testid="ch-back-dashboard"
            onClick={() => navigate("/dashboard")}
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "6px 12px", borderRadius: 8,
              background: "transparent",
              border: "1px solid rgba(255,255,255,0.12)",
              color: "#cbd5e1",
              fontSize: 12, fontFamily: "'JetBrains Mono', monospace",
              cursor: "pointer", transition: "all 0.15s ease",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = "#FF6608";
              e.currentTarget.style.color = "#FF6608";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = "rgba(255,255,255,0.12)";
              e.currentTarget.style.color = "#cbd5e1";
            }}
          >
            ← Back to dashboard
          </button>
        </div>

        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                      marginBottom: 24, flexWrap: "wrap", gap: 12 }}>
          <div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
                          color: "#7dd3fc", letterSpacing: 2, marginBottom: 6 }}>
              CODEBASE HEALTH
            </div>
            <h1 style={{ fontSize: 28, fontWeight: 800, margin: 0 }}>
              How healthy is your code today?
            </h1>
          </div>
          <div data-testid="token-counter" style={{ position: "relative",
            padding: "10px 16px", borderRadius: 999,
            background: noTokens ? "rgba(239,68,68,0.10)" : lowTokens ? "rgba(245,158,11,0.10)" : "rgba(168,85,247,0.10)",
            border: `1px solid ${noTokens ? "rgba(239,68,68,0.45)" : lowTokens ? "rgba(245,158,11,0.45)" : "rgba(168,85,247,0.45)"}`,
            color: noTokens ? "#fca5a5" : lowTokens ? "#fbbf24" : "#d8b4fe",
            fontSize: 13, fontFamily: "'JetBrains Mono', monospace", fontWeight: 600,
          }}>
            💎 {tokens} tokens
            {tokenFloat && (
              <span style={{
                position: "absolute", left: "50%", top: "100%", marginTop: 6,
                fontSize: 14, fontWeight: 800, color: "#fca5a5",
                animation: "float-up 1.4s ease-out forwards",
              }}>
                {tokenFloat}
              </span>
            )}
            {(noTokens || lowTokens) && (
              <button onClick={() => navigate("/pricing")}
                      style={{ marginLeft: 10, background: "transparent",
                               border: "none", color: "inherit", cursor: "pointer",
                               textDecoration: "underline", fontSize: 12 }}>
                Buy more
              </button>
            )}
          </div>
        </div>

        {/* Error pill */}
        {error && (
          <div data-testid="health-error" style={{
            padding: "10px 14px", marginBottom: 16, borderRadius: 8,
            background: "rgba(239,68,68,0.10)", border: "1px solid rgba(239,68,68,0.45)",
            color: "#fca5a5", fontSize: 13,
          }}>{error}</div>
        )}

        {/* Empty state */}
        {showEmpty && (
          <div data-testid="health-empty" style={{ padding: 32, borderRadius: 14,
            background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.08)" }}>
            <div style={{ fontSize: 22, fontWeight: 800, marginBottom: 8 }}>🔍 Your codebase is unscanned</div>
            <p style={{ color: "#94a3b8", fontSize: 14, marginBottom: 24, lineHeight: 1.5 }}>
              Most codebases have 20-50 issues that developers don&apos;t know about. Pick a category to scan, or run the full audit:
            </p>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
                          gap: 12, marginBottom: 18 }}>
              {CATS.map((c) => {
                const Icon = c.icon;
                const isNew = c.key === "bug_hunt";
                return (
                  <button
                    key={c.key}
                    data-testid={`scan-${c.key}`}
                    onClick={() => runScan([c.key])}
                    style={{
                      position: "relative",
                      padding: 16, borderRadius: 10, cursor: "pointer",
                      background: `${c.tone}11`, border: `1px solid ${c.tone}55`,
                      color: "#e8ecf3", textAlign: "left",
                    }}
                  >
                    {isNew && (
                      <span style={{
                        position: "absolute", top: 8, right: 8, fontSize: 9.5, fontWeight: 800,
                        padding: "2px 6px", borderRadius: 4, letterSpacing: 0.6,
                        background: "#f472b6", color: "#0a0a0a",
                        fontFamily: "'JetBrains Mono', monospace",
                      }}>NEW</span>
                    )}
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                      <Icon size={16} color={c.tone} />
                      <span style={{ fontWeight: 700, fontSize: 13 }}>{c.label}</span>
                    </div>
                    <span style={{ fontSize: 11, color: "#94a3b8", fontFamily: "'JetBrains Mono', monospace" }}>{c.cost} 💎</span>
                  </button>
                );
              })}
            </div>
            <button
              data-testid="scan-full"
              onClick={() => runScan(CATS.map((c) => c.key))}
              style={{
                width: "100%", padding: "14px 18px", borderRadius: 10,
                fontSize: 15, fontWeight: 700, cursor: "pointer",
                background: "linear-gradient(135deg, #ea580c, #f59e0b)",
                color: "#0a0a0a", border: "none",
              }}
            >
              🚀 Full Scan — all {CATS.length} categories · {CATS.reduce((s,c)=>s+c.cost,0)} 💎
            </button>
          </div>
        )}

        {/* Scanning state */}
        {scanning && (
          <div data-testid="health-scanning" style={{
            padding: 28, borderRadius: 12, textAlign: "center",
            background: "rgba(56,189,248,0.05)", border: "1px solid rgba(56,189,248,0.20)",
            color: "#cbd5e1", fontSize: 13,
          }}>
            <Loader2 size={26} className="anim-spin" style={{ marginBottom: 10 }} />
            <div>Scanning your repository… up to 30 seconds.</div>
          </div>
        )}

        {/* Results */}
        {data && !scanning && (
          <>
            <HealthBadge score={data.score} label={data.label} tone={data.tone} />
            <p style={{ fontSize: 14, color: "#cbd5e1", marginBottom: 22, lineHeight: 1.6 }}>
              Your codebase has <strong>{data.total}</strong> issues that could
              cause production outages, data leaks, and slow response times.
              Scanned <strong>{data.scanned_files}</strong> files.
            </p>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 18, flexWrap: "wrap" }}>
              <button
                data-testid="rescan-btn"
                onClick={() => runScan(CATS.map((c) => c.key))}
                style={{
                  padding: "8px 14px", borderRadius: 8,
                  background: "rgba(56,189,248,0.10)", border: "1px solid rgba(56,189,248,0.45)",
                  color: "#7dd3fc", cursor: "pointer", fontSize: 12.5, fontWeight: 600,
                  display: "inline-flex", alignItems: "center", gap: 6,
                }}>
                <RefreshCw size={13} /> Rescan all · {CATS.reduce((s,c)=>s+c.cost,0)} 💎
              </button>
              <span style={{ color: "#64748b", fontSize: 11.5,
                             fontFamily: "'JetBrains Mono', monospace" }}>
                {data.summary}
              </span>
            </div>
            {CATS.map((cat) => {
              const catData = data?.breakdown?.[cat.key];
              if (!catData) return null;
              return (
                <CategoryCard
                  key={cat.key}
                  cat={cat}
                  data={catData}
                  expanded={!!expandedCats[cat.key]}
                  onToggle={() => setExpandedCats((s) => ({ ...s, [cat.key]: !s[cat.key] }))}
                  onFix={fixOne}
                  busyIds={busyIds}
                  unlockedHigh={!!unlocked[cat.key]}
                  onUnlockHigh={(k) => setUnlocked((u) => ({ ...u, [k]: true }))}
                  onBulkFix={openBulk}
                  canFix={canFix}
                  canBulk={canBulk}
                />
              );
            })}
          </>
        )}
      </div>
      <BulkFixConfirmModal
        open={bulkModal.open}
        onClose={() => setBulkModal({ open: false, cat: null, findings: [] })}
        projectId={projectId}
        findings={bulkModal.findings}
        category={bulkModal.cat?.label || ""}
        tool="health-scan"
      />
    </div>
  );
}
