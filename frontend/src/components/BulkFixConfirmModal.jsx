/**
 * BulkFixConfirmModal.jsx — Iter 212m-121
 *
 * Cost-preview confirm dialog the CategoryCard opens before kicking
 * off a bulk fix.  Pulls the live preview from
 *   POST /api/aurem-dev/fix-pipeline/preview
 * so the user always sees the real token + USD cost AND the founder
 * `⚡ FREE` chip when applicable.  The Confirm button then POSTs
 * to /fix-pipeline/bulk and fires `aurem:open-fix-progress` so the
 * FixProgressDrawer takes over.
 *
 * Props:
 *   open       — bool
 *   onClose    — fn()
 *   projectId  — string
 *   findings   — array of finding objects (already normalised)
 *   category   — display label (e.g. "Security", "Bug Hunt") for the title
 */
import React, { useEffect, useState } from "react";
import { X, Zap, GitMerge, Loader2 } from "lucide-react";
import { api } from "../lib/api";

export default function BulkFixConfirmModal({
  open, onClose, projectId, findings, category,
}) {
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    if (!open || !findings?.length) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.post("/fix-pipeline/preview", {
      project_id: projectId, findings,
    })
      .then((r) => {
        if (!cancelled) setPreview(r?.data || r);
      })
      .catch((e) => {
        if (!cancelled) setError(
          e?.response?.data?.detail || e?.message || "Preview failed");
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open, projectId, findings]);

  if (!open) return null;

  async function confirm() {
    if (!preview || starting) return;
    if (!preview.can_proceed) return;
    setStarting(true);
    setError(null);
    try {
      // Iter 212m-179 — hard cap per run (GitHub secondary rate limit).
      const capped = preview.bulk_max
        ? findings.slice(0, preview.bulk_max) : findings;
      const r = await api.post("/fix-pipeline/bulk", {
        project_id: projectId, findings: capped,
      });
      const payload = r?.data || r;
      if (payload?.job_id) {
        window.dispatchEvent(new CustomEvent("aurem:open-fix-progress", {
          detail: { job_id: payload.job_id, total: payload.count || findings.length },
        }));
        onClose?.();
      } else {
        setError("No job_id returned");
      }
    } catch (e) {
      const detail = e?.response?.data?.detail;
      const code   = typeof detail === "object" ? detail.error : detail;
      if (code === "insufficient_tokens") {
        setError(`Need ${detail.needed} tokens, have ${detail.balance}.`);
      } else {
        setError(typeof detail === "string" ? detail
                 : detail?.message || e?.message || "Bulk fix failed to start");
      }
    } finally {
      setStarting(false);
    }
  }

  const founder = !!preview?.is_unlimited;
  const overCap = !!(preview?.bulk_max
    && (preview?.total_requested ?? findings?.length ?? 0) > preview.bulk_max);

  return (
    <div
      data-testid="bulk-fix-modal-scrim"
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 1400,
        background: "rgba(6,8,13,0.65)", backdropFilter: "blur(6px)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
    >
      <div
        data-testid="bulk-fix-modal"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(440px, 92%)",
          background: "#0d1018",
          border: "1px solid rgba(255,255,255,0.10)",
          borderRadius: 12, padding: 24,
          boxShadow: "0 40px 90px rgba(0,0,0,0.55)",
          color: "#e8ecf3",
        }}
      >
        <header style={{
          display: "flex", alignItems: "center", gap: 10, marginBottom: 18,
        }}>
          <GitMerge size={20} color={founder ? "#fb923c" : "#7dd3fc"} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 15, fontWeight: 700 }}>
              Bulk fix · {category}
            </div>
            <div style={{ fontSize: 11, color: "#94a3b8",
                          fontFamily: "'JetBrains Mono', monospace",
                          marginTop: 2 }}>
              {findings?.length || 0} findings · sequential commits
            </div>
          </div>
          <button onClick={onClose}
            data-testid="bulk-fix-modal-close"
            style={{
              padding: 6, background: "transparent",
              border: "1px solid rgba(255,255,255,0.12)",
              borderRadius: 6, cursor: "pointer", color: "#94a3b8",
            }}><X size={14} /></button>
        </header>

        {loading && (
          <div style={{ padding: "16px 0", color: "#94a3b8", fontSize: 12 }}>
            <Loader2 size={14} className="anim-spin" /> Calculating cost…
          </div>
        )}

        {preview && !loading && (
          <div style={{ marginBottom: 18 }}>
            {overCap && (
              <div
                data-testid="bulk-fix-cap-warning"
                style={{
                  padding: "10px 12px", marginBottom: 12, borderRadius: 8,
                  background: "rgba(251,191,36,0.08)",
                  border: "1px solid rgba(251,191,36,0.45)",
                  color: "#fcd34d", fontSize: 11, lineHeight: 1.5,
                  fontFamily: "'JetBrains Mono', monospace",
                }}
              >
                ⚠ Max {preview.bulk_max} fixes per run (GitHub rate-limit
                protection). Only the first {preview.count} of{" "}
                {preview.total_requested} findings will run now — start
                another batch for the rest.
              </div>
            )}
            {founder ? (
              <div
                data-testid="bulk-fix-founder-chip"
                style={{
                  padding: "16px 18px", borderRadius: 10,
                  background: "linear-gradient(135deg, rgba(251,146,60,0.14), rgba(251,146,60,0.04))",
                  border: "1px solid rgba(251,146,60,0.50)",
                  display: "flex", alignItems: "center", gap: 12,
                }}
              >
                <Zap size={22} color="#fb923c" />
                <div>
                  <div style={{ fontSize: 14, fontWeight: 700, color: "#fb923c" }}>
                    Founder — FREE
                  </div>
                  <div style={{ fontSize: 11, color: "#fdba74", marginTop: 2 }}>
                    {preview.count} commits · no token deduction
                  </div>
                </div>
              </div>
            ) : (
              <div style={{
                padding: "14px 16px", borderRadius: 10,
                background: "rgba(255,255,255,0.025)",
                border: "1px solid rgba(255,255,255,0.08)",
              }}>
                <div style={{
                  display: "flex", justifyContent: "space-between",
                  fontSize: 13, color: "#cbd5e1", marginBottom: 6,
                }}>
                  <span>Findings to fix</span>
                  <strong>{preview.count}</strong>
                </div>
                <div style={{
                  display: "flex", justifyContent: "space-between",
                  fontSize: 13, color: "#cbd5e1", marginBottom: 6,
                }}>
                  <span>Token cost</span>
                  <strong data-testid="bulk-fix-token-cost">
                    {preview.tokens_cost.toLocaleString()} 💎
                  </strong>
                </div>
                <div style={{
                  display: "flex", justifyContent: "space-between",
                  fontSize: 13, color: "#cbd5e1", marginBottom: 10,
                }}>
                  <span>USD equivalent</span>
                  <strong data-testid="bulk-fix-usd-cost">
                    ${preview.usd_cost.toFixed(4)}
                  </strong>
                </div>
                <div style={{
                  fontSize: 11, color: preview.can_proceed ? "#86efac" : "#fca5a5",
                  fontFamily: "'JetBrains Mono', monospace",
                  paddingTop: 8,
                  borderTop: "1px solid rgba(255,255,255,0.04)",
                }}>
                  {preview.can_proceed
                    ? `Balance after: ${(preview.balance - preview.tokens_cost).toLocaleString()} 💎`
                    : `Short by ${preview.shortfall.toLocaleString()} tokens — top up first`}
                </div>
              </div>
            )}
            <p style={{
              fontSize: 11, color: "#94a3b8", marginTop: 12, lineHeight: 1.5,
            }}>
              Fixes run sequentially — each one creates an
              <code style={{ color: "#cbd5e1" }}> aurem/fix-* </code>
              branch and a draft PR for review.  No direct pushes to
              <code style={{ color: "#cbd5e1" }}> main</code>.
            </p>
          </div>
        )}

        {error && (
          <div style={{
            padding: 10, marginBottom: 14, borderRadius: 8,
            background: "rgba(239,68,68,0.08)",
            border: "1px solid rgba(239,68,68,0.4)",
            color: "#fca5a5", fontSize: 12,
          }}>{error}</div>
        )}

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button
            onClick={onClose}
            data-testid="bulk-fix-modal-cancel"
            style={{
              padding: "8px 14px", borderRadius: 8,
              background: "transparent",
              border: "1px solid rgba(255,255,255,0.14)",
              color: "#cbd5e1", cursor: "pointer", fontSize: 12,
            }}
          >Cancel</button>
          <button
            onClick={confirm}
            disabled={!preview?.can_proceed || starting || loading}
            data-testid="bulk-fix-modal-confirm"
            style={{
              padding: "8px 16px", borderRadius: 8,
              background: founder
                ? "linear-gradient(135deg, #fb923c, #ea580c)"
                : "linear-gradient(135deg, #38bdf8, #0284c7)",
              border: "none", color: "#fff", cursor: starting ? "wait" : "pointer",
              fontSize: 12, fontWeight: 700, letterSpacing: 0.3,
              opacity: (!preview?.can_proceed || starting || loading) ? 0.5 : 1,
              display: "inline-flex", alignItems: "center", gap: 6,
            }}
          >
            {starting && <Loader2 size={12} className="anim-spin" />}
            {founder
              ? (overCap
                  ? `⚡ Fix first ${preview?.count} — FREE`
                  : "⚡ Fix all — FREE")
              : `Fix ${preview?.count ?? findings?.length} now`}
          </button>
        </div>
      </div>
    </div>
  );
}
