/**
 * SecretScanCard.jsx — Iter 212m-120 (Phase 1 frontend)
 *
 * Renders the latest CI secret-scan results (trufflehog) for a given
 * repo. Pulls from GET /vanguard/ci-findings?repo=owner/repo&limit=5
 * — the JWT-protected dashboard endpoint added in Iter 212m-120.
 *
 * Two visual variants share the same fetch + render logic:
 *   • variant="dashboard" — compact floating chip, sits next to the
 *     ShipStreakWidget. Hidden when no project is active or no CI
 *     run has posted yet. Red ring + count when verified secrets are
 *     present; green check otherwise.
 *   • variant="drawer"   — wider card mounted inside the Security
 *     Scan drawer, above the in-process Vanguard findings list.
 *     Lists the last 5 runs with timestamps, verified counts, and
 *     a "View on GitHub" link for each.
 *
 * Auto-refresh: re-fetches when `repo` changes and on the
 * `aurem:ci-findings-refresh` custom event (which the dashboard
 * can fire after a manual deploy / push).
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  ShieldCheck, ShieldAlert, ExternalLink, Loader2, GitCommit,
  ChevronDown, ChevronRight,
} from "lucide-react";
import { api } from "../lib/api";

function timeAgo(iso) {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (!t || Number.isNaN(t)) return "";
  const sec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (sec < 60)   return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

export default function SecretScanCard({
  repoOwner,
  repoName,
  variant = "dashboard",          // "dashboard" | "drawer"
  defaultExpanded = false,
}) {
  const repo = repoOwner && repoName ? `${repoOwner}/${repoName}` : null;
  const [runs, setRuns]       = useState([]);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [error, setError]     = useState(null);

  const fetchRuns = useCallback(async () => {
    if (!repo) { setRuns([]); return; }
    setLoading(true);
    setError(null);
    try {
      const r = await api.get(
        `/vanguard/ci-findings?repo=${encodeURIComponent(repo)}&limit=5`,
      );
      const payload = r?.data || r;
      setRuns(Array.isArray(payload?.runs) ? payload.runs : []);
    } catch (e) {
      // 403 = not your repo (cross-tenant). 404/503 = no data yet.
      // All are silent in dashboard variant; surfaced only in drawer.
      setError(e?.response?.status === 403
        ? "You don't own this repo"
        : (e?.response?.data?.detail || e?.message || "Fetch failed"));
      setRuns([]);
    } finally {
      setLoading(false);
    }
  }, [repo]);

  useEffect(() => { fetchRuns(); }, [fetchRuns]);

  useEffect(() => {
    const h = () => fetchRuns();
    window.addEventListener("aurem:ci-findings-refresh", h);
    return () => window.removeEventListener("aurem:ci-findings-refresh", h);
  }, [fetchRuns]);

  const latest = runs[0] || null;
  const verifiedNow = latest?.verified_count || 0;
  const totalNow    = latest?.total_count || 0;
  const hasVerified = verifiedNow > 0;

  // ── Dashboard variant: compact pill ────────────────────────────────
  if (variant === "dashboard") {
    if (!repo || (runs.length === 0 && !loading)) return null;
    return (
      <button
        type="button"
        data-testid="secret-scan-pill"
        onClick={() => {
          // Open the Vanguard drawer where the full card lives.
          window.dispatchEvent(new CustomEvent("aurem:open-vanguard"));
        }}
        title={hasVerified
          ? `${verifiedNow} verified secret(s) detected by trufflehog`
          : "Last CI secret scan: clean"}
        style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          padding: "5px 10px", borderRadius: 999,
          fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
          cursor: "pointer", lineHeight: 1,
          background: hasVerified
            ? "rgba(239,68,68,0.10)" : "rgba(34,197,94,0.06)",
          color: hasVerified ? "#fca5a5" : "#86efac",
          border: `1px solid ${hasVerified
            ? "rgba(239,68,68,0.45)" : "rgba(34,197,94,0.30)"}`,
          transition: "all 120ms",
        }}
      >
        {loading
          ? <Loader2 size={11} className="anim-spin" />
          : hasVerified
            ? <ShieldAlert size={11} />
            : <ShieldCheck size={11} />}
        <span data-testid="secret-scan-pill-label">
          {loading
            ? "Scan…"
            : hasVerified
              ? `${verifiedNow} secret${verifiedNow === 1 ? "" : "s"}`
              : "Secrets: clean"}
        </span>
      </button>
    );
  }

  // ── Drawer variant: full card with last-5 timeline ─────────────────
  return (
    <div
      data-testid="secret-scan-card-drawer"
      style={{
        padding: "12px 16px",
        borderBottom: "1px solid var(--border, rgba(255,255,255,0.06))",
        background: hasVerified
          ? "linear-gradient(180deg, rgba(239,68,68,0.06), transparent)"
          : "rgba(255,255,255,0.015)",
      }}
    >
      <button
        type="button"
        data-testid="secret-scan-card-toggle"
        onClick={() => setExpanded((v) => !v)}
        style={{
          display: "flex", alignItems: "center", gap: 8,
          width: "100%", padding: 0,
          background: "transparent", border: "none", cursor: "pointer",
          color: "var(--text, #e8ecf3)", textAlign: "left",
        }}
      >
        {hasVerified
          ? <ShieldAlert size={14} color="#fca5a5" />
          : <ShieldCheck size={14} color="#86efac" />}
        <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: 0.2 }}>
          CI Secret Scan (Trufflehog)
        </div>
        <div style={{ flex: 1 }} />
        {loading && <Loader2 size={11} className="anim-spin" />}
        {!loading && hasVerified && (
          <span
            data-testid="secret-scan-verified-badge"
            style={{
              padding: "2px 6px", fontSize: 10, fontWeight: 600,
              borderRadius: 999, background: "rgba(239,68,68,0.20)",
              color: "#fca5a5",
              border: "1px solid rgba(239,68,68,0.45)",
            }}
          >
            {verifiedNow} verified
          </span>
        )}
        {!loading && !hasVerified && totalNow > 0 && (
          <span style={{
            padding: "2px 6px", fontSize: 10,
            borderRadius: 999, background: "rgba(250,204,21,0.10)",
            color: "#fde68a",
            border: "1px solid rgba(250,204,21,0.30)",
          }}>
            {totalNow} pattern{totalNow === 1 ? "" : "s"}
          </span>
        )}
        {!loading && totalNow === 0 && runs.length > 0 && (
          <span style={{
            padding: "2px 6px", fontSize: 10,
            borderRadius: 999, background: "rgba(34,197,94,0.10)",
            color: "#86efac",
            border: "1px solid rgba(34,197,94,0.30)",
          }}>
            clean
          </span>
        )}
        {expanded
          ? <ChevronDown size={12} />
          : <ChevronRight size={12} />}
      </button>

      {expanded && (
        <div style={{ marginTop: 10 }} data-testid="secret-scan-card-body">
          {error && (
            <div style={{
              fontSize: 11, color: "#fca5a5",
              padding: "6px 8px", borderRadius: 4,
              background: "rgba(239,68,68,0.08)",
            }}>
              {error}
            </div>
          )}
          {!error && runs.length === 0 && !loading && (
            <div style={{
              fontSize: 11, color: "var(--text-dim, #9aa3b2)",
              padding: "8px 0",
            }}>
              No CI runs posted yet. Push to <code>main</code> to trigger
              the trufflehog scan, or check the Actions tab on GitHub.
            </div>
          )}
          {runs.length > 0 && (
            <ul style={{
              listStyle: "none", padding: 0, margin: 0,
              display: "flex", flexDirection: "column", gap: 6,
            }}>
              {runs.map((r, i) => (
                <li
                  key={`${r.commit}-${r.scanner}-${i}`}
                  data-testid={`secret-scan-run-${i}`}
                  style={{
                    display: "flex", alignItems: "center", gap: 8,
                    padding: "6px 8px", borderRadius: 4,
                    background: r.verified_count > 0
                      ? "rgba(239,68,68,0.06)" : "rgba(255,255,255,0.02)",
                    border: `1px solid ${r.verified_count > 0
                      ? "rgba(239,68,68,0.25)" : "rgba(255,255,255,0.04)"}`,
                    fontSize: 11,
                  }}
                >
                  <GitCommit
                    size={11}
                    color={r.verified_count > 0 ? "#fca5a5" : "#9aa3b2"}
                  />
                  <code style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    color: "var(--text, #e8ecf3)",
                  }}>
                    {(r.commit || "").slice(0, 7)}
                  </code>
                  <span style={{ color: "var(--text-dim, #9aa3b2)" }}>
                    {r.branch || "—"}
                  </span>
                  <div style={{ flex: 1 }} />
                  {r.verified_count > 0 && (
                    <span style={{
                      padding: "1px 6px", fontSize: 10, fontWeight: 600,
                      borderRadius: 999, background: "rgba(239,68,68,0.18)",
                      color: "#fca5a5",
                    }}>
                      {r.verified_count} verified
                    </span>
                  )}
                  <span style={{
                    color: "var(--text-dim, #9aa3b2)",
                    fontFamily: "'JetBrains Mono', monospace",
                  }}>
                    {timeAgo(r.created_at)}
                  </span>
                  {r.run_url && (
                    <a
                      href={r.run_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      data-testid={`secret-scan-run-link-${i}`}
                      title="Open GitHub Actions run"
                      style={{
                        display: "inline-flex", alignItems: "center",
                        color: "var(--text-dim, #9aa3b2)",
                      }}
                    >
                      <ExternalLink size={11} />
                    </a>
                  )}
                </li>
              ))}
            </ul>
          )}

          {/* Detail findings panel for the latest run only — keeps the
              drawer scrollable without exploding the DOM. */}
          {latest?.findings?.length > 0 && (
            <details style={{ marginTop: 10 }}>
              <summary
                data-testid="secret-scan-latest-findings-toggle"
                style={{
                  cursor: "pointer", fontSize: 11,
                  color: "var(--text-dim, #9aa3b2)",
                  fontFamily: "'JetBrains Mono', monospace",
                }}
              >
                Latest run findings ({latest.findings.length})
              </summary>
              <ul style={{
                listStyle: "none", padding: "8px 0 0 0", margin: 0,
                display: "flex", flexDirection: "column", gap: 4,
              }}>
                {latest.findings.slice(0, 50).map((f, i) => (
                  <li
                    key={`${f.file}-${f.line}-${i}`}
                    data-testid={`secret-scan-finding-${i}`}
                    style={{
                      fontSize: 11,
                      padding: "4px 6px", borderRadius: 3,
                      background: f.verified
                        ? "rgba(239,68,68,0.06)" : "rgba(250,204,21,0.04)",
                      borderLeft: `2px solid ${f.verified
                        ? "rgba(239,68,68,0.45)" : "rgba(250,204,21,0.35)"}`,
                      color: "var(--text, #e8ecf3)",
                    }}
                  >
                    <div style={{
                      display: "flex", gap: 6,
                      fontFamily: "'JetBrains Mono', monospace",
                    }}>
                      <span style={{
                        color: f.verified ? "#fca5a5" : "#fde68a",
                        fontWeight: 600,
                      }}>
                        {f.detector}
                      </span>
                      <span style={{ color: "var(--text-dim, #9aa3b2)" }}>
                        {f.file}:{f.line}
                      </span>
                      {f.verified && (
                        <span style={{
                          marginLeft: "auto",
                          padding: "0 4px",
                          fontSize: 9, fontWeight: 700,
                          borderRadius: 999,
                          background: "rgba(239,68,68,0.18)",
                          color: "#fca5a5",
                        }}>
                          VERIFIED
                        </span>
                      )}
                    </div>
                    {f.redacted && (
                      <div style={{
                        marginTop: 2, fontSize: 10,
                        color: "var(--text-dim, #9aa3b2)",
                        fontFamily: "'JetBrains Mono', monospace",
                      }}>
                        <code>{f.redacted}</code>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
