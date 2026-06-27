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
import { X, ShieldCheck, ShieldAlert, Loader2, RefreshCw, FileWarning } from "lucide-react";
import { api } from "../lib/api";

const CACHE_TTL_MS = 5 * 60 * 1000;
const _cache = new Map();   // project_id → { at, data }

const SEV_ORDER = ["critical", "high", "medium", "low"];
const SEV_COLORS = {
  critical: { bg: "rgba(239,68,68,0.10)", fg: "#fca5a5", border: "rgba(239,68,68,0.45)" },
  high:     { bg: "rgba(249,115,22,0.10)", fg: "#fdba74", border: "rgba(249,115,22,0.45)" },
  medium:   { bg: "rgba(250,204,21,0.10)", fg: "#fde68a", border: "rgba(250,204,21,0.42)" },
  low:      { bg: "rgba(125,211,252,0.10)", fg: "#bae6fd", border: "rgba(125,211,252,0.42)" },
};

export default function SecurityScanDrawer({ open, onClose, projectId, projectLabel }) {
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState(null);
  const [data, setData]         = useState(null);
  const [cachedAt, setCachedAt] = useState(null);

  const fetchScan = useCallback(async (force = false) => {
    if (!projectId) return;
    if (!force) {
      const hit = _cache.get(projectId);
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
      const res = await api.post("/security-scan/run", { project_id: projectId });
      const payload = res?.data || res;
      _cache.set(projectId, { at: Date.now(), data: payload });
      setData(payload);
      setCachedAt(Date.now());
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || "Scan failed";
      setError(typeof msg === "string" ? msg : "Scan failed");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  // Auto-fetch on open.
  useEffect(() => {
    if (open && projectId) {
      fetchScan(false);
    }
  }, [open, projectId, fetchScan]);

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

        {/* Body */}
        <div style={{ flex: 1, overflowY: "auto", padding: 16 }}>
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
              Scanning repository… this can take 10–20s
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
              {/* Summary tiles */}
              <div
                data-testid="security-scan-summary"
                style={{
                  display: "grid", gridTemplateColumns: "repeat(4, 1fr)",
                  gap: 8, marginBottom: 16,
                }}
              >
                {SEV_ORDER.map((sev) => {
                  const n = summary.by_severity?.[sev] || 0;
                  const c = SEV_COLORS[sev];
                  return (
                    <div
                      key={sev}
                      data-testid={`security-scan-tile-${sev}`}
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

              {/* Meta strip */}
              <div style={{
                display: "flex", justifyContent: "space-between",
                fontSize: 11, color: "var(--text-dim, #9aa3b2)",
                marginBottom: 12, fontFamily: "'JetBrains Mono', monospace",
              }}>
                <span>{data.scanned_files} files scanned</span>
                {cachedAt && (
                  <span title="Cached result">
                    cached • {Math.round((Date.now() - cachedAt) / 1000)}s ago
                  </span>
                )}
                {data.truncated && (
                  <span style={{ color: "#fdba74" }}>showing top 500 findings</span>
                )}
              </div>

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
                        {list.map((f, i) => (
                          <li
                            key={`${f.file}:${f.line}:${f.rule_id}:${i}`}
                            data-testid={`security-scan-finding-${sev}`}
                            style={{
                              padding: "10px 12px", marginBottom: 6,
                              borderRadius: 8,
                              background: c.bg,
                              border: `1px solid ${c.border}`,
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
                          </li>
                        ))}
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
          Static scan • findings only • no auto-fixes
        </footer>
      </aside>
    </>
  );
}
