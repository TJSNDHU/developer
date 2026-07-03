/**
 * Settings.jsx — Iter 212m-180: standalone settings WINDOW.
 *
 * The old page rendered inside the legacy <Shell> (old sidebar chrome).
 * Founder wants the avatar-menu entries to open a clean popup-style
 * window: no sidebar, v2 (ds2) design language, tabbed sections, and a
 * back button that returns to wherever the user came from.
 *
 * Entry points: sidebar avatar dropdown (Edit Profile / Settings),
 * Stripe redirect (/settings?session_id=cs_xxx → Plans tab), old
 * #pricing anchors, direct URL.
 */
import React, { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  ArrowLeft, User, KeyRound, Receipt, Plug, ShieldCheck, Coins,
} from "lucide-react";
import { api, getUser } from "../lib/api";
import { trackPurchase } from "../lib/analytics";
import GitHubCard from "../components/GitHubCard";
import VercelCard from "../components/VercelCard";
import PricingCards from "../components/PricingCards";
import OraWrapped from "../components/OraWrapped";
import ReferralShare from "../components/ReferralShare";
import TrustLevelCard from "../components/TrustLevelCard";

const TABS = [
  { id: "profile",      label: "Profile",       icon: User },
  { id: "plans",        label: "Plans & Usage", icon: Receipt },
  { id: "integrations", label: "Integrations",  icon: Plug },
  { id: "vault",        label: "Vault",         icon: KeyRound },
];

export default function Settings() {
  const location = useLocation();
  const navigate = useNavigate();
  const [me, setMe]         = useState(getUser());
  const [usage, setUsage]   = useState(null);
  const [audit, setAudit]   = useState([]);
  const [billingMsg, setBillingMsg] = useState("");

  const params = new URLSearchParams(location.search);
  const hasStripeSession = !!params.get("session_id");
  const initialTab =
    hasStripeSession || location.hash === "#pricing"
      ? "plans"
      : (TABS.some((t) => t.id === params.get("tab"))
          ? params.get("tab") : "profile");
  const [tab, setTab] = useState(initialTab);

  useEffect(() => {
    if (!getUser()) { navigate("/login", { replace: true }); return; }
    api.get("/auth/me").then((r) => r.data?.user && setMe(r.data.user)).catch(() => {});
    api.get("/usage/me").then((r) => setUsage(r.data)).catch(() => {});
    api.get("/vault/audit-log").then((r) => setAudit(r.data?.entries || r.data?.log || [])).catch(() => {});
    // eslint-disable-next-line
  }, []);

  // Stripe redirects to /settings?session_id=cs_xxx — poll until the
  // tier flip lands, then refresh the user.
  useEffect(() => {
    const sid = params.get("session_id");
    if (!sid) return;
    let cancelled = false;
    setBillingMsg("Confirming payment…");
    (async () => {
      for (let i = 0; i < 12 && !cancelled; i++) {
        try {
          const r = await api.get(`/payments/status/${sid}`);
          if (r.data?.payment_status === "paid") {
            setBillingMsg(`Upgraded to ${r.data.tier?.toUpperCase() || "PAID"} — enjoy.`);
            const tier = (r.data.tier || "").toLowerCase();
            const PLAN_VALUE_USD = { starter: 9, pro: 19, team: 49 };
            if (tier && PLAN_VALUE_USD[tier]) {
              trackPurchase(PLAN_VALUE_USD[tier], "USD", sid);
            } else {
              trackPurchase();
            }
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
    navigate("/settings?tab=plans", { replace: true });
    return () => { cancelled = true; };
    // eslint-disable-next-line
  }, []);

  // Back → origin. Direct entries (Stripe redirect / fresh tab) have no
  // in-app history, so fall back to the dashboard.
  const goBack = () => {
    if (location.key && location.key !== "default") navigate(-1);
    else navigate("/dashboard");
  };

  const switchTab = (id) => {
    setTab(id);
    navigate(`/settings?tab=${id}`, { replace: true });
  };

  const initial = (me?.name || me?.email || "?").trim().charAt(0).toUpperCase();

  return (
    <div className="ds2-root" data-theme="dark" data-testid="settings-window"
      style={{ minHeight: "100vh", background: "var(--ds2-bg, #0A0A0A)" }}>
      <div style={{
        position: "fixed", inset: 0, pointerEvents: "none",
        background: "radial-gradient(900px 400px at 80% -10%, rgba(255,102,8,0.07), transparent 60%)",
      }} />

      <div style={{
        maxWidth: 880, margin: "0 auto", padding: "28px 20px 60px",
        position: "relative", animation: "ds2SettingsIn .28s ease both",
      }}>
        {/* ── Window header ── */}
        <div style={{
          display: "flex", alignItems: "center", gap: 14, marginBottom: 22,
        }}>
          <button
            data-testid="settings-back-btn"
            onClick={goBack}
            style={{
              display: "inline-flex", alignItems: "center", gap: 7,
              padding: "8px 14px", borderRadius: 999,
              background: "var(--panel)", border: "1px solid var(--border)",
              color: "var(--text-dim)", fontSize: 12.5, cursor: "pointer",
              fontFamily: "inherit",
              transition: "border-color .15s ease, color .15s ease",
            }}
            onMouseEnter={(e) => { e.currentTarget.style.color = "var(--text)"; e.currentTarget.style.borderColor = "var(--accent)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-dim)"; e.currentTarget.style.borderColor = "var(--border)"; }}
          >
            <ArrowLeft size={14} /> Back
          </button>

          <div style={{ flex: 1 }}>
            <h1 style={{ margin: 0, fontSize: 20, color: "var(--text)", letterSpacing: "-0.01em" }}>
              Settings
            </h1>
            <p style={{ margin: "2px 0 0", fontSize: 12, color: "var(--text-faint)" }}>
              Your account, plans, integrations &amp; security.
            </p>
          </div>

          <div data-testid="settings-user-chip" style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "6px 12px 6px 6px", borderRadius: 999,
            background: "var(--panel)", border: "1px solid var(--border)",
          }}>
            <span style={{
              width: 28, height: 28, borderRadius: "50%",
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              background: "var(--accent-soft)", color: "var(--accent)",
              fontSize: 13, fontWeight: 700,
            }}>{initial}</span>
            <span style={{ fontSize: 12, color: "var(--text-dim)", maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {me?.email || "—"}
            </span>
          </div>
        </div>

        {/* ── Tabs ── */}
        <div role="tablist" style={{
          display: "flex", gap: 6, marginBottom: 20, flexWrap: "wrap",
          padding: 5, borderRadius: 12,
          background: "var(--panel)", border: "1px solid var(--border)",
          width: "fit-content",
        }}>
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              role="tab"
              aria-selected={tab === id}
              data-testid={`settings-tab-${id}`}
              onClick={() => switchTab(id)}
              style={{
                display: "inline-flex", alignItems: "center", gap: 7,
                padding: "8px 15px", borderRadius: 8, cursor: "pointer",
                fontSize: 12.5, fontFamily: "inherit", border: "none",
                background: tab === id ? "var(--accent-soft)" : "transparent",
                color: tab === id ? "var(--accent)" : "var(--text-dim)",
                fontWeight: tab === id ? 600 : 400,
                transition: "background .15s ease, color .15s ease",
              }}
            >
              <Icon size={13} /> {label}
            </button>
          ))}
        </div>

        {billingMsg && (
          <div data-testid="billing-banner" style={{
            padding: "10px 14px", marginBottom: 16, borderRadius: 8,
            background: "rgba(255,138,42,0.08)",
            border: "1px solid rgba(255,138,42,0.32)",
            color: "var(--accent-2, #ffb347)", fontSize: 12,
          }}>{billingMsg}</div>
        )}

        {/* ── Tab content ── */}
        {tab === "profile" && (
          <div key="profile" style={{ display: "grid", gap: 18, animation: "ds2SettingsIn .22s ease both" }}>
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

            <TrustLevelCard />
            <ReferralShare />
          </div>
        )}

        {tab === "plans" && (
          <div key="plans" style={{ display: "grid", gap: 18, animation: "ds2SettingsIn .22s ease both" }}>
            <section className="card" data-testid="settings-wallet" style={{
              display: "flex", alignItems: "center", gap: 14,
              padding: "16px 24px",
            }}>
              <span style={{
                width: 34, height: 34, borderRadius: 9,
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                background: "var(--accent-soft)", color: "var(--accent)",
              }}><Coins size={16} /></span>
              <div>
                <div className="label-mini" style={{ marginBottom: 2 }}>token wallet</div>
                <div style={{ fontSize: 18, color: "var(--text)", fontWeight: 600 }}>
                  {me?.is_unlimited ? "∞ Unlimited" : (me?.tokens_remaining ?? "—")}
                </div>
              </div>
            </section>

            <section className="card" data-testid="settings-pricing">
              <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 4, display: "flex", alignItems: "center", gap: 8 }}>
                <Receipt size={14} /> Plans
              </h3>
              <p style={{ fontSize: 12, color: "var(--text-faint)", margin: "0 0 16px" }}>
                Flat fee. No token surprises. Cancel any time.
              </p>
              <PricingCards currentTier={me?.tier || usage?.tier || "free"} />
            </section>

            <section className="card" data-testid="settings-wrapped">
              <OraWrapped defaultPeriod="this_month" />
            </section>
          </div>
        )}

        {tab === "integrations" && (
          <div key="integrations" style={{ display: "grid", gap: 18, animation: "ds2SettingsIn .22s ease both" }}>
            <GitHubCard />
            <VercelCard />
          </div>
        )}

        {tab === "vault" && (
          <div key="vault" style={{ display: "grid", gap: 18, animation: "ds2SettingsIn .22s ease both" }}>
            <section className="card" data-testid="settings-vault">
              <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 4, display: "flex", alignItems: "center", gap: 8 }}>
                <ShieldCheck size={14} /> Vault audit log
              </h3>
              <p style={{ fontSize: 12, color: "var(--text-faint)", margin: "0 0 14px" }}>
                Every read / write against your encrypted key vault.
              </p>
              {audit.length === 0 ? (
                <p style={{ fontSize: 13, color: "var(--text-faint)", margin: 0 }}>No key activity yet.</p>
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
        )}
      </div>

      <style>{`
        @keyframes ds2SettingsIn {
          from { opacity: 0; transform: translateY(6px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
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
