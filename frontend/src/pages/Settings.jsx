/**
 * Settings.jsx — Profile, plans, GitHub linkage, vault audit.
 */
import React, { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { User, KeyRound, Receipt } from "lucide-react";
import Shell, { PageHeader } from "../components/Shell";
import { api, getUser } from "../lib/api";
import GitHubCard from "../components/GitHubCard";
import PricingCards from "../components/PricingCards";
import OraWrapped from "../components/OraWrapped";

export default function Settings() {
  const [me, setMe]         = useState(getUser());
  const [usage, setUsage]   = useState(null);
  const [audit, setAudit]   = useState([]);
  const [billingMsg, setBillingMsg] = useState("");
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    api.get("/auth/me").then((r) => r.data?.user && setMe(r.data.user)).catch(() => {});
    api.get("/usage/me").then((r) => setUsage(r.data)).catch(() => {});
    api.get("/vault/audit-log").then((r) => setAudit(r.data?.entries || r.data?.log || [])).catch(() => {});
  }, []);

  // Stripe redirects to /settings?session_id=cs_xxx — poll until the
  // tier flip lands, then refresh the user.
  useEffect(() => {
    const p = new URLSearchParams(location.search);
    const sid = p.get("session_id");
    if (!sid) return;
    let cancelled = false;
    setBillingMsg("Confirming payment…");
    (async () => {
      for (let i = 0; i < 12 && !cancelled; i++) {
        try {
          const r = await api.get(`/payments/status/${sid}`);
          if (r.data?.payment_status === "paid") {
            setBillingMsg(`Upgraded to ${r.data.tier?.toUpperCase() || "PAID"} — enjoy.`);
            const me2 = await api.get("/auth/me");
            if (me2.data?.user) setMe(me2.data.user);
            return;
          }
        } catch { /* keep trying */ }
        await new Promise((r) => setTimeout(r, 2000));
      }
      if (!cancelled) setBillingMsg("Payment is still processing. Refresh in a minute.");
    })();
    // Strip session_id from the URL so a reload doesn't re-poll.
    navigate("/settings", { replace: true });
    return () => { cancelled = true; };
    // eslint-disable-next-line
  }, []);

  return (
    <Shell requireAuth>
      <PageHeader
        eyebrow="account"
        title="Settings"
        sub="Profile, plans, GitHub linkage, and API key vault audit trail."
      />

      {billingMsg && (
        <div data-testid="billing-banner" style={{
          padding: "10px 14px", marginBottom: 16, borderRadius: 6,
          background: "rgba(255,138,42,0.08)",
          border: "1px solid rgba(255,138,42,0.32)",
          color: "var(--accent-2, #ffb347)", fontSize: 12,
        }}>{billingMsg}</div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: 18, maxWidth: 920 }}>
        <section className="card" data-testid="settings-profile">
          <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 14, display: "flex", alignItems: "center", gap: 8 }}>
            <User size={14} /> Profile
          </h3>
          <Row k="email" v={me?.email || "—"} />
          <Row k="name" v={me?.name || "—"} />
          <Row k="user id" v={me?.user_id || "—"} />
          <Row k="tier" v={me?.tier || usage?.tier || "free"} />
          {usage && usage.monthly_task_cap != null && (
            <Row k="tasks this month"
                 v={`${usage.tasks_this_month} / ${usage.monthly_task_cap}`} />
          )}
          {usage && usage.monthly_task_cap == null && (
            <Row k="tasks this month" v={`${usage.tasks_this_month} / unlimited`} />
          )}
        </section>

        <GitHubCard />

        {/* Pricing — 4-tier flat-fee cards, side-by-side */}
        <section
          id="pricing"
          className="card"
          data-testid="settings-pricing"
          style={{ gridColumn: "1 / -1" }}
        >
          <h3 style={{
            fontSize: 14, color: "var(--text)",
            margin: 0, marginBottom: 4,
            display: "flex", alignItems: "center", gap: 8,
          }}>
            <Receipt size={14} /> Plans
          </h3>
          <p style={{
            fontSize: 12, color: "var(--text-faint)",
            margin: "0 0 16px",
          }}>
            Flat fee. No token surprises. Cancel any time.
          </p>
          <PricingCards currentTier={me?.tier || usage?.tier || "free"} />
        </section>

        {/* Your activity — OraWrapped mini embed so plan + usage live on the same page */}
        <section
          className="card"
          data-testid="settings-wrapped"
          style={{ gridColumn: "1 / -1" }}
        >
          <OraWrapped defaultPeriod="this_month" />
        </section>

        <section className="card" data-testid="settings-vault" style={{ gridColumn: "1 / -1" }}>
          <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 14, display: "flex", alignItems: "center", gap: 8 }}>
            <KeyRound size={14} /> Vault audit log
          </h3>
          {audit.length === 0 ? (
            <p style={{ fontSize: 13, color: "var(--text-faint)" }}>No key activity yet.</p>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 8 }}>
              {audit.slice(0, 10).map((e, i) => (
                <li key={i} style={{
                  fontSize: 12, color: "var(--text-dim)",
                  fontFamily: "'JetBrains Mono', monospace",
                  padding: "6px 0", borderBottom: "1px solid var(--border)",
                }}>
                  [{e.ts || e.created_at || "?"}] {e.action || e.event || "?"} · {e.key_name || e.key || "—"}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </Shell>
  );
}

function Row({ k, v }) {
  return (
    <div style={{
      display: "flex", gap: 14, alignItems: "baseline",
      padding: "8px 0", borderBottom: "1px solid var(--border)",
      fontSize: 13,
    }}>
      <span style={{
        fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
        textTransform: "uppercase", letterSpacing: "0.15em",
        color: "var(--text-faint)", width: 130,
      }}>{k}</span>
      <span style={{ color: "var(--text)" }}>{v}</span>
    </div>
  );
}
