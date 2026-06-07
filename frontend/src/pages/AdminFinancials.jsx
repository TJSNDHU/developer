/**
 * pages/AdminFinancials.jsx — Live Financial Command Center.
 *
 * Real MongoDB data + editable founder inputs + live FX.
 *  • User counts come from `dev_users` (live) by default, but founder
 *    can flip "Manual override" to play with hypothetical numbers.
 *  • Cash in bank + dev salary are persisted in `financial_settings`.
 *  • Every input change → POST /admin/financials/settings → full
 *    recompute returned and rendered in one atomic swap.
 *  • 6-month roadmap chart drawn pure-SVG (no chart lib bloat).
 */
import React, { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api, getToken } from "../lib/api";

const TIER_TINT = {
  free:    "#888d99",
  starter: "#6dd4a1",
  pro:     "#ff8a2a",
  team:    "#a78bfa",
};

function dollars(n, sign = false) {
  if (n === null || n === undefined) return "—";
  const s = sign && n > 0 ? "+" : "";
  return `${s}$${Math.round(Math.abs(n) * 100) / 100}`.replace("$-", "-$");
}
function k(n) { return n >= 1000 ? `$${Math.round(n/100)/10}k` : `$${Math.round(n)}`; }

function NumberInput({ label, value, onChange, testid, prefix, suffix }) {
  return (
    <label data-testid={`field-${testid}`} style={{
      display: "flex", flexDirection: "column", gap: 6,
      fontSize: 11, color: "var(--text-dim)", letterSpacing: ".04em",
      textTransform: "uppercase",
    }}>
      <span>{label}</span>
      <div style={{
        display: "flex", alignItems: "stretch",
        background: "var(--panel)",
        border: "1px solid var(--border)",
        borderRadius: 6,
      }}>
        {prefix && <span style={{
          padding: "10px 4px 10px 12px", color: "var(--text-faint)",
        }}>{prefix}</span>}
        <input
          data-testid={`input-${testid}`}
          type="number"
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          min={0}
          style={{
            flex: 1,
            padding: "10px 12px",
            background: "transparent",
            border: "none", outline: "none",
            color: "var(--text)",
            fontSize: 16, fontWeight: 500,
            fontFamily: "inherit",
          }}
        />
        {suffix && <span style={{
          padding: "10px 12px 10px 4px", color: "var(--text-faint)",
        }}>{suffix}</span>}
      </div>
    </label>
  );
}

function MetricCard({ label, value, sub, tone, testid }) {
  const colors = {
    danger: "#ff6b6b", warn: "#ffb454", ok: "#6dd4a1", neutral: "var(--text)",
  };
  return (
    <div data-testid={`metric-${testid}`} style={{
      padding: "16px 16px 14px",
      background: "var(--panel)",
      border: "1px solid var(--border)",
      borderRadius: 8,
      display: "flex", flexDirection: "column", gap: 4,
    }}>
      <div style={{
        fontSize: 11, color: "var(--text-dim)",
        letterSpacing: ".04em", textTransform: "uppercase",
      }}>{label}</div>
      <div style={{
        fontSize: 22, fontWeight: 600,
        color: colors[tone] || colors.neutral, marginTop: 2,
      }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: "var(--text-faint)", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

/* Tiny pure-SVG line chart for the 6-month roadmap */
function PnLChart({ data }) {
  if (!data || !data.length) return null;
  const W = 700, H = 240, P = 40;
  const xs = data.map((_, i) => P + (i * (W - 2 * P)) / (data.length - 1));
  const all = data.flatMap(d => [d.revenue, d.total_cost, d.net_profit]);
  const minY = Math.min(0, ...all), maxY = Math.max(1, ...all);
  const yScale = v => H - P - ((v - minY) / (maxY - minY)) * (H - 2 * P);
  const path = (key) =>
    data.map((d, i) => `${i ? "L" : "M"}${xs[i]},${yScale(d[key])}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto" }}>
      {/* gridlines */}
      {[0, 0.25, 0.5, 0.75, 1].map((t, i) => {
        const y = P + t * (H - 2 * P);
        return <line key={i} x1={P} y1={y} x2={W - P} y2={y}
                     stroke="rgba(255,255,255,0.04)" />;
      })}
      {/* zero line */}
      {minY < 0 && (
        <line x1={P} y1={yScale(0)} x2={W - P} y2={yScale(0)}
              stroke="rgba(255,255,255,0.10)" strokeDasharray="2 4" />
      )}
      {/* lines */}
      <path d={path("total_cost")}  fill="none" stroke="#ff6b6b" strokeWidth={2}
            strokeDasharray="4 4" />
      <path d={path("revenue")}     fill="none" stroke="#6dd4a1" strokeWidth={2.5} />
      <path d={path("net_profit")}  fill="none" stroke="#5eb3f5" strokeWidth={2.5} />
      {/* dots */}
      {data.map((d, i) => (
        <g key={i}>
          <circle cx={xs[i]} cy={yScale(d.revenue)}    r="3" fill="#6dd4a1" />
          <circle cx={xs[i]} cy={yScale(d.total_cost)} r="3" fill="#ff6b6b" />
          <circle cx={xs[i]} cy={yScale(d.net_profit)} r="3" fill="#5eb3f5" />
          <text x={xs[i]} y={H - 14} fill="var(--text-faint)"
                fontSize="10" textAnchor="middle">{d.label}</text>
        </g>
      ))}
      {/* y labels */}
      {[0, 0.5, 1].map((t, i) => {
        const v = maxY - t * (maxY - minY);
        return (
          <text key={i} x={6} y={P + t * (H - 2 * P) + 3}
                fill="var(--text-faint)" fontSize="10">{k(v)}</text>
        );
      })}
    </svg>
  );
}

export default function AdminFinancials() {
  const nav = useNavigate();
  const [data, setData] = useState(null);
  const [err, setErr]   = useState("");
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setErr("");
    try {
      const r = await api.get("/admin/financials", {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      setData(r.data);
      setDraft(r.data.settings);
    } catch (e) {
      const detail = (e && e.response && e.response.data && e.response.data.detail) || "";
      setErr(detail || (e && e.message) || "Failed to load.");
    }
  }, []);

  const save = useCallback(async (patch) => {
    setSaving(true);
    try {
      const r = await api.post("/admin/financials/settings", patch, {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      setData(r.data);
      setDraft(r.data.settings);
    } catch (e) {
      const detail = (e && e.response && e.response.data && e.response.data.detail) || "";
      setErr(detail || (e && e.message) || "Save failed.");
    } finally {
      setSaving(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (!data) {
    return (
      <div data-testid="loading" style={{ padding: 40, color: "var(--text-dim)" }}>
        {err || "Loading financials…"}
      </div>
    );
  }

  const m = data.metrics;
  const cad = (usd) => (usd != null ? `≈ C$${Math.round(usd * data.fx.rate)}` : "");
  const u   = data.users;

  return (
    <div data-testid="admin-financials-page" style={{
      maxWidth: 1280, margin: "0 auto", padding: "32px 24px 80px",
      color: "var(--text)",
    }}>
      <button
        data-testid="admin-back"
        onClick={() => nav("/admin/overview")}
        style={{
          fontSize: 11, color: "var(--text-dim)",
          background: "transparent", border: "none",
          cursor: "pointer", marginBottom: 16,
        }}
      >← Back to admin</button>

      <header style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "flex-end", marginBottom: 28,
      }}>
        <div>
          <h1 style={{
            fontSize: 28, fontWeight: 500, letterSpacing: "-0.02em",
            margin: 0,
          }}>Financial Command Center</h1>
          <p style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 6 }}>
            Live MongoDB data · USD→CAD rate <b>{data.fx.rate}</b>{" "}
            <span style={{ color: "var(--text-faint)" }}>({data.fx.source})</span>
            {" "}·{" "}MRR source: <code>{m.mrr_source}</code>
          </p>
        </div>
        <button
          data-testid="reload-btn"
          onClick={load}
          style={{
            padding: "8px 14px", fontSize: 12,
            background: "transparent", color: "var(--text-dim)",
            border: "1px solid var(--border)", borderRadius: 5,
            cursor: "pointer",
          }}>↻ Reload</button>
      </header>

      {err && (
        <div data-testid="error" style={{
          fontSize: 12, padding: "10px 14px", marginBottom: 16,
          background: "rgba(255,107,107,0.06)",
          border: "1px solid rgba(255,107,107,0.2)",
          color: "var(--danger)", borderRadius: 5,
        }}>{err}</div>
      )}

      {/* ── EDITABLE INPUTS ─────────────────────────────────────── */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(3, 1fr)",
        gap: 14, marginBottom: 28,
      }}>
        <NumberInput
          label="Free users (live from DB)"
          value={u.free}
          onChange={(v) => save({ manual_overrides_enabled: true, manual_free: v,
            manual_starter: u.starter, manual_pro: u.pro, manual_team: u.team })}
          testid="free-users"
        />
        <NumberInput
          label="Starter ($9) users"
          value={u.starter}
          onChange={(v) => save({ manual_overrides_enabled: true, manual_starter: v,
            manual_free: u.free, manual_pro: u.pro, manual_team: u.team })}
          testid="starter-users"
        />
        <NumberInput
          label="Pro ($19) users"
          value={u.pro}
          onChange={(v) => save({ manual_overrides_enabled: true, manual_pro: v,
            manual_free: u.free, manual_starter: u.starter, manual_team: u.team })}
          testid="pro-users"
        />
        <NumberInput
          label="Team ($49) users"
          value={u.team}
          onChange={(v) => save({ manual_overrides_enabled: true, manual_team: v,
            manual_free: u.free, manual_starter: u.starter, manual_pro: u.pro })}
          testid="team-users"
        />
        <NumberInput
          label="Cash in bank (USD)"
          value={draft?.cash_in_bank_usd || 0}
          onChange={(v) => save({ cash_in_bank_usd: v })}
          testid="cash-bank"
          prefix="$"
        />
        <NumberInput
          label="Dev salary (USD/mo)"
          value={draft?.dev_salary_usd || 0}
          onChange={(v) => save({ dev_salary_usd: v })}
          testid="dev-salary"
          prefix="$"
          suffix="/mo"
        />
      </div>

      <div style={{ fontSize: 11, color: "var(--text-faint)", marginBottom: 12 }}>
        Data source: <code>{data.user_source}</code> · real DB has{" "}
        {data.real_user_counts.total} users · {data.maxx_used_total} Maxx tasks
        this month
        {data.settings.manual_overrides_enabled && (
          <button
            data-testid="reset-overrides"
            onClick={() => save({ manual_overrides_enabled: false })}
            style={{
              marginLeft: 12, fontSize: 11, color: "var(--accent)",
              background: "transparent", border: "none", cursor: "pointer",
              textDecoration: "underline",
            }}>Reset to live DB →</button>
        )}
        {saving && <span style={{ marginLeft: 12, color: "var(--accent)" }}>saving…</span>}
      </div>

      {/* ── HEADLINE METRICS ────────────────────────────────────── */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(5, 1fr)",
        gap: 12, marginBottom: 16,
      }}>
        <MetricCard testid="mrr"        label="MRR"
          value={dollars(m.mrr_usd)}
          tone={m.mrr_usd === 0 ? "danger" : m.mrr_usd < m.total_burn_usd ? "warn" : "ok"}
          sub={m.mrr_usd < m.total_burn_usd ? "below break-even" : "above break-even"} />
        <MetricCard testid="net-profit" label="Net profit/mo"
          value={dollars(m.net_profit_usd, true)}
          tone={m.net_profit_usd < 0 ? "danger" : "ok"}
          sub={`${cad(m.net_profit_usd)}/mo`} />
        <MetricCard testid="gross-margin" label="Gross margin"
          value={`${m.gross_margin_pct}%`}
          tone={m.gross_margin_pct > 80 ? "ok" : m.gross_margin_pct > 50 ? "warn" : "danger"}
          sub={m.gross_margin_pct > 80 ? "excellent" : "tune costs"} />
        <MetricCard testid="ai-cost"    label="AI cost/mo"
          value={dollars(m.ai_cost_usd)}
          tone="neutral"
          sub="all APIs combined" />
        <MetricCard testid="total-burn" label="Total burn/mo"
          value={dollars(m.total_burn_usd)}
          tone="danger"
          sub={`infra + AI + dev · ${cad(m.total_burn_usd)}`} />
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(3, 1fr)",
        gap: 12, marginBottom: 28,
      }}>
        <MetricCard testid="runway"     label="Cash runway"
          value={m.cash_runway_days == null ? "∞" : `${m.cash_runway_days}d`}
          tone={m.cash_runway_days == null ? "ok" :
                m.cash_runway_days < 30 ? "danger" :
                m.cash_runway_days < 90 ? "warn" : "ok"}
          sub={m.cash_runway_days == null ? "profitable" :
               m.cash_runway_days < 30 ? "critical!" :
               m.cash_runway_days < 90 ? "tight" : "safe"} />
        <MetricCard testid="cac"        label="CAC (organic)"
          value={dollars(m.cac_usd)}
          tone="ok"
          sub="GitHub + ProductHunt" />
        <MetricCard testid="break-even" label="Break-even at"
          value={m.break_even_users != null ? `${m.break_even_users} users` : "—"}
          tone="warn"
          sub={m.break_even_need != null ? `need ${m.break_even_need} more Pro` : ""} />
      </div>

      {/* ── 2-COL: cost tables ──────────────────────────────────── */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: 14, marginBottom: 28,
      }}>
        <div data-testid="cost-per-task" style={{
          padding: "18px 20px", background: "var(--panel)",
          border: "1px solid var(--border)", borderRadius: 8,
        }}>
          <h3 style={{ fontSize: 11, color: "var(--text-dim)",
                       letterSpacing: ".08em", textTransform: "uppercase",
                       margin: "0 0 14px" }}>
            Cost per task — real model costs
          </h3>
          {data.cost_per_task.map((row, i) => (
            <div key={i} style={{
              display: "flex", justifyContent: "space-between",
              padding: "8px 0", fontSize: 13,
              borderTop: i > 0 ? "1px solid var(--border)" : "none",
            }}>
              <span style={{ color: "var(--text)" }}>{row.label}</span>
              <span style={{
                fontFamily: "monospace", fontWeight: 600,
                color: row.highlight === "ok" ? "#6dd4a1" :
                       row.highlight === "warn" ? "#ffb454" :
                       row.highlight === "danger" ? "#ff6b6b" :
                       "var(--text)",
              }}>${row.usd.toFixed(4)}</span>
            </div>
          ))}
        </div>

        <div data-testid="fixed-costs" style={{
          padding: "18px 20px", background: "var(--panel)",
          border: "1px solid var(--border)", borderRadius: 8,
        }}>
          <h3 style={{ fontSize: 11, color: "var(--text-dim)",
                       letterSpacing: ".08em", textTransform: "uppercase",
                       margin: "0 0 14px" }}>
            Fixed infra costs — monthly
          </h3>
          {Object.entries(data.fixed_costs).map(([k, v], i) => (
            <div key={k} style={{
              display: "flex", justifyContent: "space-between",
              padding: "8px 0", fontSize: 13,
              borderTop: i > 0 ? "1px solid var(--border)" : "none",
            }}>
              <span style={{
                color: k.includes("Dev pay") ? "var(--danger)" : "var(--text)",
              }}>{k}</span>
              <span style={{
                fontFamily: "monospace",
                color: k.includes("Dev pay") ? "var(--danger)" : "var(--text)",
              }}>${v.toFixed(2)}</span>
            </div>
          ))}
          <div style={{
            display: "flex", justifyContent: "space-between",
            padding: "10px 0 0", marginTop: 4,
            fontSize: 13, fontWeight: 600,
            borderTop: "2px solid var(--border)",
          }}>
            <span>Total fixed</span>
            <span style={{ color: "var(--danger)", fontFamily: "monospace" }}>
              ${m.total_fixed_usd.toFixed(2)}/mo · {cad(m.total_fixed_usd)}/mo
            </span>
          </div>
        </div>
      </div>

      {/* ── Per-tier margins ─────────────────────────────────────── */}
      <div data-testid="tier-margins" style={{
        padding: "18px 20px", background: "var(--panel)",
        border: "1px solid var(--border)", borderRadius: 8,
        marginBottom: 28,
      }}>
        <h3 style={{ fontSize: 11, color: "var(--text-dim)",
                     letterSpacing: ".08em", textTransform: "uppercase",
                     margin: "0 0 14px" }}>
          Gross margin per tier (per user/mo)
        </h3>
        {data.tier_margins.map((t, i) => (
          <div key={t.tier} style={{
            display: "grid",
            gridTemplateColumns: "120px 100px 120px 80px",
            gap: 12, alignItems: "center",
            padding: "10px 0", fontSize: 14,
            borderTop: i > 0 ? "1px solid var(--border)" : "none",
          }}>
            <span style={{
              fontWeight: 600, color: TIER_TINT[t.tier], textTransform: "capitalize",
            }}>{t.tier}</span>
            <span style={{ color: "var(--text-dim)", fontSize: 12 }}>
              {t.tasks_avg} tasks avg
            </span>
            <span style={{
              fontFamily: "monospace", fontWeight: 600,
              color: t.gross_profit > 0 ? "#6dd4a1" : "#ff6b6b",
            }}>{dollars(t.gross_profit, true)}</span>
            <span style={{ color: "var(--text-dim)", fontSize: 12, textAlign: "right" }}>
              {t.gross_margin_pct}%
            </span>
          </div>
        ))}
      </div>

      {/* ── 6-month P&L chart ────────────────────────────────────── */}
      <div data-testid="pnl-roadmap" style={{
        padding: "18px 20px", background: "var(--panel)",
        border: "1px solid var(--border)", borderRadius: 8,
      }}>
        <h3 style={{ fontSize: 11, color: "var(--text-dim)",
                     letterSpacing: ".08em", textTransform: "uppercase",
                     margin: "0 0 14px" }}>
          6-month P&L roadmap (conservative)
        </h3>
        <PnLChart data={data.projection} />
        <div style={{
          display: "flex", gap: 24, fontSize: 11,
          color: "var(--text-dim)", marginTop: 12,
        }}>
          <span><span style={{
            display: "inline-block", width: 12, height: 2, background: "#6dd4a1",
            verticalAlign: "middle", marginRight: 6,
          }}/>Revenue</span>
          <span><span style={{
            display: "inline-block", width: 12, height: 2, background: "#ff6b6b",
            verticalAlign: "middle", marginRight: 6, borderTop: "1px dashed #ff6b6b",
          }}/>Total costs (incl. dev)</span>
          <span><span style={{
            display: "inline-block", width: 12, height: 2, background: "#5eb3f5",
            verticalAlign: "middle", marginRight: 6,
          }}/>Net profit</span>
        </div>
      </div>
    </div>
  );
}
