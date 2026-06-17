/**
 * Pricing.jsx — Public pricing route (/pricing).
 *
 * Iter 176 — fetches the authenticated user's real tier so the
 * "CURRENT" badge actually reflects what they're paying for. Anonymous
 * visitors (no token) get `currentTier=""` so no plan is mis-labelled
 * as their current. PricingCards.upgrade() itself handles redirect to
 * /login when an anon visitor clicks "Upgrade".
 */
import { useEffect, useState } from "react";
import Shell from "../components/Shell";
import PricingCards from "../components/PricingCards";
import { api, getToken } from "../lib/api";

export default function Pricing() {
  const [currentTier, setCurrentTier] = useState("");

  useEffect(() => {
    // Only call the authenticated endpoint when a token actually
    // exists — otherwise we just splash a 401 in the console for
    // nothing. Anon visitors see no "CURRENT" badge anywhere.
    if (!getToken()) return;
    let alive = true;
    api.get("/payments/my-plan")
      .then((r) => { if (alive) setCurrentTier(r.data?.tier || ""); })
      .catch(() => { /* logged-in user but plan lookup failed —
                        leave the badge off, never throw a banner */ });
    return () => { alive = false; };
  }, []);

  return (
    <Shell>
      <div
        data-testid="pricing-page"
        style={{ maxWidth: 900, margin: "0 auto", padding: "48px 24px" }}
      >
        <div style={{ textAlign: "center", marginBottom: 48 }}>
          <span className="eyebrow">pricing</span>
          <h1 className="serif" style={{ fontSize: 36, marginTop: 8 }}>
            Flat pricing. No token meters.
          </h1>
          <p style={{ color: "var(--text-dim)", marginTop: 8 }}>
            Ship 5 tasks or 500 — same price.
          </p>
        </div>
        <PricingCards currentTier={currentTier} />
      </div>
    </Shell>
  );
}
