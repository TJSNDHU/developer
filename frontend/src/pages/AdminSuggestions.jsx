/**
 * pages/AdminSuggestions.jsx — Iter 212m-193
 *
 * Founder Suggestion Box — admin review panel.
 * Fetches from `GET /suggestions/admin/list` and posts decisions to
 * `POST /suggestions/admin/{sid}/decide`.
 *
 * LLM analysis is explicitly labelled "AI analysis — not a decision"
 * next to the tick/cross so the founder never treats the summary
 * as the call. The tick/cross is the only real decision.
 */
import React, { useCallback, useEffect, useState } from "react";
import { api, getToken } from "../lib/api";

const STATUS_OPTIONS = [
  { value: "pending",  label: "Pending"  },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "",         label: "All"       },
];

export default function AdminSuggestions() {
  const [status, setStatus] = useState("pending");
  const [rows,   setRows]   = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(() => new Set());
  const [deciding, setDeciding] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const h = { Authorization: `Bearer ${getToken()}` };
      const q = status ? `?status=${status}` : "";
      const r = await api.get(`/suggestions/admin/list${q}`, { headers: h });
      setRows(r.data?.suggestions || []);
    } catch (e) {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => { load(); }, [load]);

  async function decide(sid, decision) {
    if (deciding) return;
    setDeciding(sid);
    try {
      const h = { Authorization: `Bearer ${getToken()}` };
      await api.post(
        `/suggestions/admin/${sid}/decide`,
        { decision },
        { headers: h }
      );
      // Optimistic local update — keep the row in place, mark it decided.
      setRows((prev) => prev.map((r) =>
        r.suggestion_id === sid
          ? { ...r, admin_decision: decision, decided_at: new Date().toISOString() }
          : r
      ));
    } catch (e) {
      alert(`Failed to record decision: ${e?.message || "unknown"}`);
    } finally {
      setDeciding(null);
    }
  }

  function toggle(sid) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(sid)) next.delete(sid);
      else next.add(sid);
      return next;
    });
  }

  return (
    <div style={{ padding: "24px 20px", maxWidth: 1000 }}
         data-testid="admin-suggestions-panel">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 18 }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0, color: "var(--text)" }}>
          Founder Suggestions
        </h1>
        <div style={{ display: "flex", gap: 6 }}>
          {STATUS_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setStatus(opt.value)}
              data-testid={`admin-suggestions-filter-${opt.value || "all"}`}
              style={{
                padding: "5px 12px", fontSize: 11,
                background: status === opt.value ? "var(--accent-2)" : "var(--panel-2)",
                color: status === opt.value ? "#000" : "var(--text-dim)",
                border: "1px solid var(--border)", borderRadius: 4,
                cursor: "pointer", textTransform: "uppercase", letterSpacing: 0.6,
              }}>
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {loading && (
        <div style={{ color: "var(--text-faint)", fontSize: 13, padding: "24px 0" }}>
          Loading suggestions…
        </div>
      )}

      {!loading && rows.length === 0 && (
        <div data-testid="admin-suggestions-empty"
             style={{ color: "var(--text-faint)", fontSize: 13, padding: "24px 0" }}>
          No suggestions in this bucket yet.
        </div>
      )}

      {!loading && rows.map((r) => {
        const la = r.llm_analysis;
        const failed = r.analysis_failed && !la;
        const isOpen = expanded.has(r.suggestion_id);
        const decided = r.admin_decision !== "pending";
        return (
          <div
            key={r.suggestion_id}
            data-testid={`admin-suggestion-row-${r.suggestion_id}`}
            style={{
              background: "var(--panel-1)", border: "1px solid var(--border)",
              borderRadius: 6, padding: 14, marginBottom: 10,
              opacity: decided ? 0.72 : 1,
            }}>
            {/* Top row — user + timestamp + decision badge */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 8 }}>
              <div style={{ fontSize: 11, color: "var(--text-dim)" }}>
                <b style={{ color: "var(--text)" }}>{r.email || r.user_id}</b>
                {r.tier && <> · <span style={{ textTransform: "uppercase" }}>{r.tier}</span></>}
                {r.project_id && <> · project <code style={{ fontFamily: "'JetBrains Mono',monospace" }}>{r.project_id}</code></>}
              </div>
              <div style={{ fontSize: 10, color: "var(--text-faint)", fontFamily: "'JetBrains Mono',monospace" }}>
                {new Date(r.created_at).toLocaleString()}
                {" · "}
                <span style={{
                  color:
                    r.admin_decision === "approved" ? "#4ade80" :
                    r.admin_decision === "rejected" ? "#fb7185" :
                    "var(--text-faint)",
                }}>
                  {r.admin_decision.toUpperCase()}
                </span>
              </div>
            </div>

            {/* Suggestion text — the actual user input */}
            <div style={{ fontSize: 13.5, lineHeight: 1.55, color: "var(--text)", marginBottom: 10 }}>
              {r.text}
            </div>

            {/* AI analysis chip / expandable block */}
            {la && (
              <div style={{
                background: "rgba(59, 130, 246, 0.05)",
                border: "1px solid rgba(59, 130, 246, 0.2)",
                borderRadius: 4, padding: "8px 12px", marginBottom: 10, fontSize: 12,
              }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <div style={{ color: "#93c5fd" }}>
                    <span style={{ textTransform: "uppercase", letterSpacing: 0.6, fontSize: 10, opacity: 0.8, marginRight: 8 }}>
                      AI analysis — not a decision
                    </span>
                    <span style={{ color: "var(--text)" }}>{la.summary}</span>
                  </div>
                  <button
                    onClick={() => toggle(r.suggestion_id)}
                    data-testid={`admin-suggestion-toggle-${r.suggestion_id}`}
                    style={{
                      fontSize: 11, background: "transparent", border: "none",
                      color: "#93c5fd", cursor: "pointer", padding: "0 4px",
                    }}>
                    {isOpen ? "hide" : "details"}
                  </button>
                </div>
                {isOpen && (
                  <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                    <div>
                      <div style={{ fontSize: 10, color: "#86efac", textTransform: "uppercase", marginBottom: 4 }}>Benefits</div>
                      {la.benefits?.length
                        ? <ul style={{ margin: 0, paddingLeft: 16, color: "var(--text-dim)" }}>
                            {la.benefits.map((b, i) => <li key={i} style={{ marginBottom: 2 }}>{b}</li>)}
                          </ul>
                        : <span style={{ color: "var(--text-faint)" }}>none</span>}
                    </div>
                    <div>
                      <div style={{ fontSize: 10, color: "#fca5a5", textTransform: "uppercase", marginBottom: 4 }}>Risks</div>
                      {la.risks?.length
                        ? <ul style={{ margin: 0, paddingLeft: 16, color: "var(--text-dim)" }}>
                            {la.risks.map((x, i) => <li key={i} style={{ marginBottom: 2 }}>{x}</li>)}
                          </ul>
                        : <span style={{ color: "var(--text-faint)" }}>none</span>}
                    </div>
                    <div style={{ gridColumn: "1 / -1", display: "flex", gap: 18, marginTop: 4, fontSize: 11, color: "var(--text-faint)" }}>
                      <span>Effort: <b style={{ color: "var(--text-dim)" }}>{la.effort_estimate}</b></span>
                      <span>Overlaps existing? <b style={{ color: "var(--text-dim)" }}>{la.overlaps_existing ? "yes" : "no"}</b></span>
                      <span>Recommendation: <b style={{
                        color: la.recommendation === "consider" ? "#4ade80"
                             : la.recommendation === "likely_skip" ? "#fb7185"
                             : "var(--text-dim)",
                      }}>{la.recommendation}</b></span>
                    </div>
                    {la.overlaps_note && la.overlaps_note !== "not_verified" && (
                      <div style={{ gridColumn: "1 / -1", fontSize: 11, color: "var(--text-faint)", fontStyle: "italic" }}>
                        overlaps note: {la.overlaps_note}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {failed && (
              <div data-testid={`admin-suggestion-analysis-failed-${r.suggestion_id}`}
                   style={{
                background: "rgba(251,146,60,0.06)", border: "1px solid rgba(251,146,60,0.28)",
                borderRadius: 4, padding: "6px 10px", marginBottom: 10, fontSize: 11, color: "#fdba74",
              }}>
                AI analysis unavailable ({r.analysis_error || "unknown error"}). Decide based on the raw suggestion text above.
              </div>
            )}

            {/* Approve / Reject */}
            <div style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "flex-end" }}>
              <span style={{ fontSize: 10, color: "var(--text-faint)", textTransform: "uppercase", letterSpacing: 0.6 }}>
                Founder decision:
              </span>
              <button
                disabled={deciding === r.suggestion_id || r.admin_decision === "approved"}
                onClick={() => decide(r.suggestion_id, "approved")}
                data-testid={`admin-suggestion-approve-${r.suggestion_id}`}
                style={{
                  padding: "5px 12px", fontSize: 12,
                  background: r.admin_decision === "approved" ? "rgba(74,222,128,0.15)" : "rgba(74,222,128,0.08)",
                  border: "1px solid rgba(74,222,128,0.4)", borderRadius: 4,
                  color: "#4ade80", cursor: r.admin_decision === "approved" ? "default" : "pointer",
                }}>
                ✓ Approve
              </button>
              <button
                disabled={deciding === r.suggestion_id || r.admin_decision === "rejected"}
                onClick={() => decide(r.suggestion_id, "rejected")}
                data-testid={`admin-suggestion-reject-${r.suggestion_id}`}
                style={{
                  padding: "5px 12px", fontSize: 12,
                  background: r.admin_decision === "rejected" ? "rgba(251,113,133,0.15)" : "rgba(251,113,133,0.08)",
                  border: "1px solid rgba(251,113,133,0.4)", borderRadius: 4,
                  color: "#fb7185", cursor: r.admin_decision === "rejected" ? "default" : "pointer",
                }}>
                ✕ Reject
              </button>
            </div>

            {r.decided_at && (
              <div style={{ fontSize: 10, color: "var(--text-faint)", marginTop: 6, textAlign: "right", fontFamily: "'JetBrains Mono',monospace" }}>
                decided {new Date(r.decided_at).toLocaleString()}
                {r.decided_by && <> · by {r.decided_by}</>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
