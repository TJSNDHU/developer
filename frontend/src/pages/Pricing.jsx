/**
 * Pricing.jsx — Public pricing route (/pricing).
 *
 * Renders the 4-tier flat-fee PricingCards inside the standard Shell
 * chrome so visitors get the same sidebar/topbar as authenticated
 * pages. `currentTier="free"` is the conservative default; the
 * Settings page passes the user's real tier when shown to logged-in
 * visitors via that surface.
 */
import Shell from "../components/Shell";
import PricingCards from "../components/PricingCards";

export default function Pricing() {
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
        <PricingCards currentTier="free" />
      </div>
    </Shell>
  );
}
