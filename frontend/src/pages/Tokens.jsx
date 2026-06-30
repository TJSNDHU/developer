/**
 * Tokens.jsx — Token balance + usage.
 */
import React, { useEffect, useState } from "react";
import { Coins } from "lucide-react";
import Shell, { PageHeader } from "../components/Shell";
import { api, getUser, setUser } from "../lib/api";

export default function Tokens() {
  const [me, setMe] = useState(getUser());
  const [streak, setStreak] = useState(null);

  useEffect(() => {
    api.get("/auth/me").then((r) => {
      if (r.data?.user) {
        setMe(r.data.user);
        setUser({ ...getUser(), ...r.data.user });
      }
    }).catch(() => {});
    api.get("/streak/me").then((r) => setStreak(r.data)).catch(() => {});
  }, []);

  return (
    <Shell requireAuth>
      <PageHeader
        eyebrow="balance"
        title="Tokens"
        sub="Track your token wallet. Refills every 24h on the free tier."
      />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 16, maxWidth: 820 }}>
        <Stat
          icon={Coins}
          label="tokens remaining"
          // Iter 212m-154 — Founder / unlimited / admin accounts bypass
          // token deduction everywhere else in the stack but the UI
          // was still rendering `tokens_remaining` literally (=0 once
          // any was deducted by /streak or /wrapped warm-up).  Founder
          // QA caught this — show "∞ Unlimited" when the auth flag
          // says the wallet is uncapped.
          value={me?.is_unlimited ? "∞ Unlimited" : (me?.tokens_remaining ?? "—")}
          testid="tokens-remaining"
        />
        <Stat icon={Coins} label="tier" value={me?.tier || "free"} testid="tokens-tier" />
        <Stat icon={Coins} label="streak (days)" value={streak?.streak_days ?? 0} testid="tokens-streak" />
      </div>
    </Shell>
  );
}

function Stat({ icon: Icon, label, value, testid }) {
  return (
    <div className="card" data-testid={testid}>
      <div style={{ display: "flex", alignItems: "center", gap: 8,
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 10, color: "var(--text-faint)",
                    textTransform: "uppercase", letterSpacing: "0.18em",
                    marginBottom: 14 }}>
        <Icon size={11} /> {label}
      </div>
      <div className="serif" style={{ fontSize: 28, color: "var(--accent-2)" }}>{value}</div>
    </div>
  );
}
