/**
 * BugHunt.jsx — Iter 212m-75 dedicated landing page for the Bug Hunt
 * security scanner (Iter 212m-73). Mirrors Landing.jsx design tokens:
 * #f59e0b accent, #0a0e1a dark bg, JetBrains Mono monospace, glass cards.
 *
 * Route: /bug-hunt
 * SEO: WebPage + SoftwareApplication JSON-LD injected at mount.
 *
 * Iter 212m-154 — authed-user redirect: if a logged-in user lands on
 * /bug-hunt we send them to the authenticated `/codebase-health`
 * scan dashboard.  Public visitors keep seeing the marketing page so
 * the SEO + conversion funnel is preserved.
 *
 * Iter 212m-157 — Admin-only gate.  Bug Hunt, Vanguard, Security
 * Scan, and Health Scan now ALL hide for non-admin users (per
 * founder spec).  Behaviour matrix:
 *   • anonymous    → marketing page (SEO preserved)
 *   • admin/founder → marketing page (consistent — they can also
 *                     reach the live scanner via the sidebar link)
 *   • logged-in non-admin → <Navigate to="/dashboard" replace>
 */
import React, { useEffect } from "react";
import { Link, Navigate } from "react-router-dom";
import { getUser, getToken, isAdminOrFounder } from "../lib/api";

const BH_CSS = `
.bh-page {
  --bg:        #0a0e1a;
  --bg-2:      #0f172a;
  --line:      rgba(255,255,255,0.08);
  --line-2:    rgba(255,255,255,0.04);
  --accent:    #f59e0b;
  --accent-2:  #ea580c;
  --accent-bg: rgba(245,158,11,0.06);
  --accent-br: rgba(245,158,11,0.3);
  --text:      #f8fafc;
  --muted-1:   #94a3b8;
  --muted-2:   #64748b;
  --muted-3:   #475569;
  --green:     #22c55e;
  --red:       #ef4444;
  --font-mono: ui-monospace, SFMono-Regular, "JetBrains Mono", "Fira Code", Menlo, monospace;
  color: var(--text);
  background: #050811;
  min-height: 100vh;
  position: relative;
  font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
}
/* Fixed hero background image — only on /bug-hunt page (scoped by .bh-page) */
.bh-page::before {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background-image: url("/bug-hunt-bg.png");
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
  opacity: 0.28;
}
/* Dark gradient overlay for readability + subtle amber glow */
.bh-page::after {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background:
    radial-gradient(900px 540px at 18% -8%, rgba(245,158,11,0.14), transparent 70%),
    radial-gradient(820px 480px at 86% 6%,  rgba(239,68,68,0.08), transparent 65%),
    linear-gradient(180deg, rgba(10,14,26,0.55) 0%, rgba(5,8,17,0.92) 100%);
}
.bh-page * { box-sizing: border-box; }
.bh-page .container { max-width: 1180px; margin: 0 auto; padding: 0 24px; position: relative; z-index: 1; }

/* Nav */
.bh-page .bh-nav {
  position: sticky; top: 0; z-index: 50;
  backdrop-filter: blur(18px);
  background: rgba(10,14,26,0.72);
  border-bottom: 1px solid var(--line);
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 32px;
}
.bh-page .nav-left { display: flex; align-items: center; gap: 14px; }
.bh-page .logo-img { width: 38px; height: 38px; border-radius: 9px; }
.bh-page .logo-text { font-family: var(--font-mono); font-weight: 800; font-size: 20px; letter-spacing: -1px; color: var(--accent); }
.bh-page .logo-text span { color: var(--muted-2); font-weight: 400; font-size: 12px; margin-left: 6px; letter-spacing: 0; }
.bh-page .nav-links { display: flex; align-items: center; gap: 22px; }
.bh-page .nav-link { font-family: var(--font-mono); font-size: 13px; color: var(--muted-1); text-decoration: none; }
.bh-page .nav-link:hover { color: var(--accent); }
.bh-page .nav-cta {
  font-family: var(--font-mono); font-size: 13px;
  background: var(--accent); color: #000; padding: 9px 18px;
  border-radius: 8px; text-decoration: none; font-weight: 700;
}
.bh-page .nav-cta:hover { opacity: 0.92; }

/* Hero */
.bh-page .hero { padding: 88px 32px 56px; text-align: center; }
.bh-page .hero-badge {
  display: inline-flex; align-items: center; gap: 8px;
  font-family: var(--font-mono); font-size: 11px;
  text-transform: uppercase; letter-spacing: 1.5px;
  background: rgba(239,68,68,0.08); color: #fca5a5;
  padding: 7px 14px; border: 1px solid rgba(239,68,68,0.35);
  border-radius: 999px; margin-bottom: 28px;
}
.bh-page .hero-badge .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--red); animation: bhPulse 1.4s ease-in-out infinite; }
@keyframes bhPulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
.bh-page .hero-title {
  font-family: var(--font-mono); font-weight: 700;
  font-size: clamp(28px, 4.4vw, 52px);
  line-height: 1.1; letter-spacing: -1.5px; margin: 0 auto 24px;
  max-width: 880px;
}
.bh-page .hero-title .hl { color: var(--accent); }
.bh-page .hero-sub {
  color: var(--muted-1); font-size: clamp(15px, 1.4vw, 18px);
  line-height: 1.65; margin: 0 auto 36px; max-width: 720px;
}
.bh-page .hero-buttons { display: flex; gap: 14px; justify-content: center; flex-wrap: wrap; margin-bottom: 16px; }
.bh-page .btn-primary, .bh-page .btn-ghost {
  font-family: var(--font-mono); font-size: 14px; padding: 14px 28px;
  border-radius: 10px; text-decoration: none; font-weight: 600;
  cursor: pointer; border: none; display: inline-block;
  transition: opacity 0.15s, transform 0.08s, background 0.15s;
}
.bh-page .btn-primary { background: linear-gradient(135deg, var(--accent), var(--accent-2)); color: #0a0a0a; }
.bh-page .btn-primary:hover { opacity: 0.94; }
.bh-page .btn-ghost { background: transparent; color: var(--muted-1); border: 1px solid var(--line); }
.bh-page .btn-ghost:hover { color: var(--text); border-color: var(--accent-br); }
.bh-page .trust-line { color: var(--muted-2); font-size: 12.5px; font-family: var(--font-mono); margin-top: 8px; }

/* Stats bar */
.bh-page .stats-bar {
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 0; margin: 56px auto 0; max-width: 980px;
  border: 1px solid var(--line); border-radius: 14px;
  background: rgba(15,23,42,0.4); overflow: hidden;
}
.bh-page .stat-cell { padding: 28px 18px; text-align: center; border-right: 1px solid var(--line-2); }
.bh-page .stat-cell:last-child { border-right: none; }
.bh-page .stat-num { font-family: var(--font-mono); font-size: 38px; font-weight: 800; color: var(--accent); line-height: 1; }
.bh-page .stat-lbl { color: var(--muted-2); font-size: 12px; margin-top: 8px; font-family: var(--font-mono); }
@media (max-width: 700px) { .bh-page .stats-bar { grid-template-columns: repeat(2, 1fr); } .bh-page .stat-cell { border-right: none; border-bottom: 1px solid var(--line-2); } }

/* Sections */
.bh-page .section { padding: 88px 32px; }
.bh-page .section-label { font-family: var(--font-mono); font-size: 11px; color: var(--accent); text-transform: uppercase; letter-spacing: 1.5px; }
.bh-page .section-title { font-family: var(--font-mono); font-weight: 700; font-size: clamp(26px, 3.6vw, 40px); margin: 10px 0 8px; letter-spacing: -1px; }
.bh-page .section-sub { color: var(--muted-1); font-size: 15px; margin-bottom: 44px; max-width: 680px; }

/* Detection cards grid (4) */
.bh-page .detect-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 18px; }
.bh-page .detect-card {
  background: rgba(15,23,42,0.55); border: 1px solid var(--line);
  border-radius: 14px; padding: 26px; backdrop-filter: blur(8px);
  transition: transform 0.2s, border-color 0.2s;
}
.bh-page .detect-card:hover { transform: translateY(-2px); border-color: var(--accent-br); }
.bh-page .detect-icon {
  width: 44px; height: 44px; border-radius: 11px; display: flex; align-items: center; justify-content: center;
  font-size: 22px; margin-bottom: 14px;
}
.bh-page .detect-icon.shield { background: rgba(245,158,11,0.14); color: var(--accent); }
.bh-page .detect-icon.bug    { background: rgba(239,68,68,0.14);  color: #f87171; }
.bh-page .detect-icon.lock   { background: rgba(56,189,248,0.14); color: #38bdf8; }
.bh-page .detect-icon.warn   { background: rgba(251,191,36,0.14); color: #fbbf24; }
.bh-page .detect-card h3 { font-family: var(--font-mono); font-size: 17px; margin: 0 0 12px; color: var(--text); }
.bh-page .detect-list { list-style: none; padding: 0; margin: 0; }
.bh-page .detect-list li {
  padding: 6px 0; color: var(--muted-1); font-size: 12.5px; line-height: 1.55;
  font-family: var(--font-mono); border-bottom: 1px dashed var(--line-2);
}
.bh-page .detect-list li:last-child { border-bottom: none; }
.bh-page .detect-list li code { color: var(--accent); background: rgba(245,158,11,0.06); padding: 1px 5px; border-radius: 3px; font-size: 11.5px; }

/* How it works */
.bh-page .how-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
.bh-page .how-step { padding: 32px 26px; border-right: 1px solid var(--line-2); background: rgba(15,23,42,0.4); }
.bh-page .how-step:last-child { border-right: none; }
.bh-page .how-num { font-family: var(--font-mono); color: var(--accent); font-size: 11px; letter-spacing: 1.5px; margin-bottom: 14px; }
.bh-page .how-icon { width: 42px; height: 42px; border-radius: 10px; background: rgba(245,158,11,0.12); color: var(--accent); display: flex; align-items: center; justify-content: center; font-size: 20px; margin-bottom: 14px; }
.bh-page .how-title { font-family: var(--font-mono); font-weight: 700; font-size: 16px; margin-bottom: 10px; }
.bh-page .how-desc { color: var(--muted-1); font-size: 13.5px; line-height: 1.6; }

/* Comparison table */
.bh-page .compare-wrap { overflow-x: auto; }
.bh-page .compare-table { width: 100%; border-collapse: separate; border-spacing: 0; border: 1px solid var(--line); border-radius: 14px; overflow: hidden; font-size: 13.5px; }
.bh-page .compare-table th, .bh-page .compare-table td { padding: 14px 18px; text-align: left; border-bottom: 1px solid var(--line-2); }
.bh-page .compare-table tr:last-child td { border-bottom: none; }
.bh-page .compare-table thead th { background: var(--bg-2); color: var(--muted-1); font-family: var(--font-mono); font-size: 11.5px; text-transform: uppercase; letter-spacing: 1px; }
.bh-page .compare-table thead th.ora-col { background: linear-gradient(135deg, rgba(245,158,11,0.16), rgba(234,88,12,0.12)); color: var(--accent); }
.bh-page .compare-table tbody td.feature-name { color: var(--text); font-weight: 500; }
.bh-page .compare-table tbody td.ora-cell { background: rgba(245,158,11,0.06); color: var(--text); border-left: 1px solid var(--accent-br); border-right: 1px solid var(--accent-br); }
.bh-page .compare-table tbody td { color: var(--muted-1); }
.bh-page .yes { color: var(--green); font-weight: 600; }
.bh-page .no  { color: var(--muted-3); }
.bh-page .compare-footnote { color: var(--muted-2); font-size: 12.5px; margin-top: 18px; line-height: 1.65; padding: 16px 20px; background: rgba(239,68,68,0.05); border: 1px solid rgba(239,68,68,0.15); border-radius: 10px; }
.bh-page .compare-footnote b { color: #fca5a5; }

/* Final CTA */
.bh-page .cta-section {
  padding: 96px 32px; text-align: center;
  background: linear-gradient(180deg, transparent 0%, rgba(245,158,11,0.04) 50%, transparent 100%);
  border-top: 1px solid var(--line);
}
.bh-page .cta-title { font-family: var(--font-mono); font-weight: 700; font-size: clamp(28px, 4.5vw, 44px); margin: 0 0 14px; letter-spacing: -1px; }
.bh-page .cta-sub { color: var(--muted-1); margin-bottom: 30px; font-size: 16px; }
.bh-page .cta-foot { color: var(--muted-2); font-family: var(--font-mono); font-size: 11.5px; margin-top: 16px; }

/* Footer */
.bh-page .footer { border-top: 1px solid var(--line); padding: 32px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 18px; }
.bh-page .footer-text { color: var(--muted-3); font-size: 12px; font-family: var(--font-mono); }
.bh-page .footer-links { display: flex; gap: 22px; flex-wrap: wrap; }
.bh-page .footer-links a { color: var(--muted-2); font-size: 12px; text-decoration: none; font-family: var(--font-mono); }
.bh-page .footer-links a:hover { color: var(--accent); }

@media (max-width: 720px) { .bh-page .nav-link { display: none; } }
`;

const SECRET_PATTERNS = [
  ["AWS access keys", "AKIA..."],
  ["GCP service account keys", "AIza..."],
  ["Stripe live keys", "sk_live_..."],
  ["GitHub PATs", "ghp_..."],
  ["JWT secrets hardcoded in source", ""],
  ["RSA private keys", "-----BEGIN RSA"],
  ["SendGrid API keys", "SG."],
  ["Slack tokens", "xox..."],
  ["Twilio auth tokens", ""],
  ["Mailgun API keys", ""],
  ["Cloudflare API tokens", ""],
  ["Heroku API keys", ""],
  ["npm tokens", ""],
  ["MongoDB connection strings with credentials", ""],
  [".env values committed in non-.env files", ""],
];

const VULN_PATTERNS = [
  ["Log4Shell — ${jndi: in any string", ""],
  ["eval() with user input", ""],
  ["exec() with user input", ""],
  ["pickle.loads() on untrusted data", ""],
  ["yaml.load() without Loader (use safe_load)", ""],
  ["subprocess with shell=True + user input", ""],
  ["os.system() with user input", ""],
  ["XML parsing without defusedxml", ""],
  ["dangerouslySetInnerHTML in React", ""],
  ["document.write() with user data", ""],
  ["innerHTML with user data", ""],
  ["SQL string concatenation (f\"SELECT...{var}\")", ""],
  ["NoSQL operator injection ($where/$expr in input)", ""],
  ["Path traversal (../ in file operations)", ""],
  ["SSRF (fetch/requests with user-supplied URL)", ""],
  ["Prototype pollution in JS", ""],
  ["Command injection via template literals", ""],
  ["Regex catastrophic backtracking", ""],
  ["Insecure random (Math.random for security)", ""],
  ["Hardcoded cryptographic keys", ""],
];

const ENDPOINT_PATTERNS = [
  ["/debug route without authentication decorator", ""],
  ["/console route without auth", ""],
  ["/admin route without auth check", ""],
  ["/actuator endpoint exposed", ""],
  ["/metrics with sensitive data", ""],
  ["Stack traces returned to API clients", ""],
  ["API keys in URL query parameters", ""],
  ["/swagger or /docs in production without auth", ""],
  ["/.env file accessible via web", ""],
  ["/phpinfo.php or debug pages", ""],
];

const CVE_PATTERNS = [
  ["requests < 2.31.0 — SSRF (CVE-2023-32681)", ""],
  ["flask < 2.3.3 — multiple CVEs", ""],
  ["django < 4.2.4 — SQL injection", ""],
  ["pillow < 10.0.1 — buffer overflow", ""],
  ["cryptography < 41.0.3 — multiple", ""],
  ["pyyaml < 6.0.1 — arbitrary code exec", ""],
  ["paramiko < 3.3.1 — auth bypass", ""],
  ["aiohttp < 3.8.5 — request smuggling", ""],
  ["jinja2 < 3.1.3 — SSTI", ""],
  ["werkzeug < 3.0.1 — path traversal", ""],
  ["numpy < 1.24.0 — buffer overflow", ""],
];

const COMPARE_ROWS = [
  ["Secret detection (15 types)",       ["yes", "All 15"],   ["no", "✗"], ["no", "✗"], ["no", "✗"], ["no", "✗"]],
  ["Vulnerable code patterns",          ["yes", "20 patterns"], ["no", "✗"], ["no", "✗"], ["no", "✗"], ["no", "✗"]],
  ["Exposed endpoint detection",        ["yes", "10 checks"], ["no", "✗"], ["no", "✗"], ["no", "✗"], ["no", "✗"]],
  ["CVE dependency scanner",            ["yes", "11 packages"], ["no", "✗"], ["no", "✗"], ["no", "Partial"], ["no", "✗"]],
  ["Runs before every commit",          ["yes", "Vanguard"], ["no", "✗"], ["no", "✗"], ["no", "Had breach"], ["no", "✗"]],
  ["Static analysis (no HTTP)",         ["yes", "Safe"], ["no", "N/A"], ["no", "N/A"], ["no", "N/A"], ["no", "N/A"]],
  ["One-click fix per finding",         ["yes", "✓"], ["no", "✗"], ["no", "✗"], ["no", "✗"], ["no", "Partial"]],
  ["Price",                             ["yes", "$9/month"], ["no", "$20/month"], ["no", "$10/month"], ["no", "$20+/month"], ["no", "$500/month"]],
];

const JSON_LD = {
  "@context": "https://schema.org",
  "@type": "WebPage",
  "name": "ORA Bug Hunt — AI Security Scanner for GitHub Repositories",
  "description": "ORA Bug Hunt detects 50+ security vulnerabilities in your codebase using static analysis. Finds secrets (15 types), vulnerable code patterns (20), exposed endpoints (10), and CVE-vulnerable dependencies (11). Used by 500+ developers. $9/month.",
  "url": "https://auremcto.com/bug-hunt",
  "keywords": "detect log4shell in code, AWS key detection in repository, CVE scanner requirements.txt, AI security scanner GitHub, find secrets in code, vulnerability scanner Python, security audit codebase, detect hardcoded API keys",
  "mainEntity": {
    "@type": "SoftwareApplication",
    "name": "ORA Bug Hunt",
    "applicationCategory": "SecurityApplication",
    "operatingSystem": "Web",
    "offers": {
      "@type": "Offer",
      "price": "9",
      "priceCurrency": "USD",
      "url": "https://auremcto.com/pricing",
    },
  },
};

function PatternList({ items }) {
  return (
    <ul className="detect-list">
      {items.map(([label, code], i) => (
        <li key={i}>
          {label}{code ? <> <code>{code}</code></> : null}
        </li>
      ))}
    </ul>
  );
}

export default function BugHunt() {
  // Iter 212m-157 — resolve auth state once at render, before any
  // hooks fire, so the conditional return below stays Rules-of-Hooks
  // safe.  Three buckets:
  //   • anon        → marketing page (SEO + conversion)
  //   • admin       → marketing page (allow access, no harm)
  //   • non-admin   → redirected to /dashboard (per founder spec)
  const me = getUser();
  const isAuthed     = !!(getToken() && me);
  const adminOrAnon  = !isAuthed || isAdminOrFounder(me);

  // SEO: title, meta description, JSON-LD injection on mount; cleanup on unmount.
  // Hook is unconditional and always runs in the same order — the
  // injected SEO tags only matter when we render the page (not when
  // we redirect).
  useEffect(() => {
    if (!adminOrAnon) return undefined;  // about to redirect, skip SEO write
    document.title = "Bug Hunt — Detect 50+ vulnerabilities in your codebase | ORA by Aurem CTO";
    let meta = document.querySelector('meta[name="description"]');
    const prev = meta ? meta.getAttribute("content") : null;
    if (!meta) {
      meta = document.createElement("meta");
      meta.setAttribute("name", "description");
      document.head.appendChild(meta);
    }
    meta.setAttribute(
      "content",
      "ORA Bug Hunt scans your GitHub repo for 50+ vulnerability patterns — secrets, vulnerable code, exposed endpoints, CVE-vulnerable dependencies. Static analysis. $9/month."
    );
    const ld = document.createElement("script");
    ld.type = "application/ld+json";
    ld.dataset.bugHunt = "1";
    ld.text = JSON.stringify(JSON_LD);
    document.head.appendChild(ld);
    return () => {
      try { document.head.removeChild(ld); } catch { /* no-op */ }
      if (prev !== null && meta) meta.setAttribute("content", prev);
    };
  }, [adminOrAnon]);

  // Iter 212m-157 — Non-admin logged-in users → /dashboard.
  // Hooks above run first to keep React's Rules of Hooks invariant.
  if (!adminOrAnon) {
    return <Navigate to="/dashboard" replace data-testid="bh-nonadmin-redirect" />;
  }

  return (
    <div className="bh-page" data-testid="bug-hunt-page">
      <style>{BH_CSS}</style>

      {/* NAV */}
      <nav className="bh-nav" data-testid="bh-nav">
        <div className="nav-left">
          <img src="/ora-icon.png" alt="ORA" className="logo-img" />
          <div className="logo-text">ORA<span> by Aurem CTO</span></div>
        </div>
        <div className="nav-links">
          <Link className="nav-link" to="/dashboard" data-testid="bh-nav-dashboard">← Dashboard</Link>
          <Link className="nav-link" to="/" data-testid="bh-nav-home">Home</Link>
          <Link className="nav-link" to="/pricing" data-testid="bh-nav-pricing">Pricing</Link>
          <Link className="nav-link" to="/login" data-testid="bh-nav-login">Sign in</Link>
          <Link className="nav-cta" to="/signup" data-testid="bh-nav-signup">Start free</Link>
        </div>
      </nav>

      <div className="container">

        {/* HERO */}
        <section className="hero">
          <div className="hero-badge" data-testid="bh-hero-badge">
            <span className="dot" /> NEW — Codebase Health Scanner
          </div>
          <h1 className="hero-title" data-testid="bh-hero-title">
            Detect <span className="hl">security vulnerabilities</span> in your codebase before they ship
          </h1>
          <p className="hero-sub" data-testid="bh-hero-sub">
            ORA Bug Hunt scans for 50+ vulnerability patterns — the same class that caused
            Lovable&apos;s CVE-2025-48757 breach in April 2026. Static analysis. No live HTTP
            requests. No risk to your production systems.
          </p>
          <div className="hero-buttons">
            <Link className="btn-primary" to="/signup" data-testid="bh-hero-cta">
              Scan my repo — Start free
            </Link>
            <a className="btn-ghost" href="#patterns" data-testid="bh-hero-secondary">
              See all 50+ patterns ↓
            </a>
          </div>
          <div className="trust-line" data-testid="bh-hero-trust">
            No credit card required · 10 free scans · Connect GitHub in 30 seconds
          </div>

          {/* STATS BAR */}
          <div className="stats-bar" data-testid="bh-stats">
            <div className="stat-cell"><div className="stat-num">50+</div><div className="stat-lbl">Vulnerability patterns</div></div>
            <div className="stat-cell"><div className="stat-num">15</div><div className="stat-lbl">Secret types detected</div></div>
            <div className="stat-cell"><div className="stat-num">11</div><div className="stat-lbl">CVE-vulnerable deps</div></div>
            <div className="stat-cell"><div className="stat-num">0</div><div className="stat-lbl">Live HTTP requests</div></div>
          </div>
        </section>

        {/* DETECTION CARDS */}
        <section className="section" id="patterns">
          <div className="section-label">What Bug Hunt detects</div>
          <h2 className="section-title">Every pattern. Every category. Every commit.</h2>
          <p className="section-sub">Pure regex static analysis. Zero LLM cost on the scan path. Findings ship with file:line + severity + plain-English explanation.</p>
          <div className="detect-grid">
            <div className="detect-card" data-testid="bh-card-secrets">
              <div className="detect-icon shield">🛡</div>
              <h3>Secret Types (15)</h3>
              <PatternList items={SECRET_PATTERNS} />
            </div>
            <div className="detect-card" data-testid="bh-card-vuln">
              <div className="detect-icon bug">🐞</div>
              <h3>Vulnerable Code Patterns (20)</h3>
              <PatternList items={VULN_PATTERNS} />
            </div>
            <div className="detect-card" data-testid="bh-card-endpoints">
              <div className="detect-icon lock">🔒</div>
              <h3>Exposed Endpoints (10)</h3>
              <PatternList items={ENDPOINT_PATTERNS} />
            </div>
            <div className="detect-card" data-testid="bh-card-cves">
              <div className="detect-icon warn">⚠</div>
              <h3>CVE-Vulnerable Dependencies (11)</h3>
              <PatternList items={CVE_PATTERNS} />
            </div>
          </div>
        </section>

        {/* HOW IT WORKS */}
        <section className="section">
          <div className="section-label">How it works</div>
          <h2 className="section-title">Three steps. Under 60 seconds.</h2>
          <p className="section-sub">No IDE. No setup. Authorise once and ORA does the rest.</p>
          <div className="how-grid">
            <div className="how-step" data-testid="bh-step-1">
              <div className="how-num">STEP 01</div>
              <div className="how-icon">⌥</div>
              <div className="how-title">Connect GitHub</div>
              <div className="how-desc">
                Authorize once. ORA reads your repository structure and file contents.
                Your code never leaves your control — scanned in-memory, never stored.
              </div>
            </div>
            <div className="how-step" data-testid="bh-step-2">
              <div className="how-num">STEP 02</div>
              <div className="how-icon">⌕</div>
              <div className="how-title">Run Bug Hunt</div>
              <div className="how-desc">
                Click the Bug Hunt tile in the Codebase Health Dashboard. ORA scans
                up to 600 files in parallel, applying all 50+ patterns. Takes under
                60 seconds for most repos.
              </div>
            </div>
            <div className="how-step" data-testid="bh-step-3">
              <div className="how-num">STEP 03</div>
              <div className="how-icon">✓</div>
              <div className="how-title">See findings with file:line</div>
              <div className="how-desc">
                Every finding shows the exact file path, line number, code snippet,
                severity (Critical/High/Medium), and a plain-English explanation of
                why it&apos;s dangerous. One-click fix queues a real code change.
              </div>
            </div>
          </div>
        </section>

        {/* COMPARISON TABLE */}
        <section className="section">
          <div className="section-label">Comparison</div>
          <h2 className="section-title">No other AI coding tool does this</h2>
          <p className="section-sub">Cursor, Copilot, Lovable, and Devin have no equivalent of Bug Hunt. Static analysis at commit time is unique to ORA.</p>
          <div className="compare-wrap">
            <table className="compare-table">
              <thead>
                <tr>
                  <th>Feature</th>
                  <th className="ora-col">ORA Bug Hunt</th>
                  <th>Cursor</th>
                  <th>GitHub Copilot</th>
                  <th>Lovable</th>
                  <th>Devin</th>
                </tr>
              </thead>
              <tbody>
                {COMPARE_ROWS.map(([feature, ...cells], i) => (
                  <tr key={i}>
                    <td className="feature-name">{feature}</td>
                    {cells.map(([cls, val], j) => (
                      <td key={j} className={j === 0 ? "ora-cell" : ""}>
                        <span className={cls === "yes" ? "yes" : "no"}>{val}</span>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="compare-footnote" data-testid="bh-cve-footnote">
            <b>*</b> Lovable had CVE-2025-48757 (April 2026) — 48 days of exposed user
            source code and database credentials due to missing authorization checks.
            ORA&apos;s Vanguard scanner detects this class of vulnerability before it
            reaches production.
          </div>
        </section>

        {/* FINAL CTA */}
        <section className="cta-section">
          <h2 className="cta-title">Start scanning your codebase today</h2>
          <p className="cta-sub">10 free Bug Hunt scans. No credit card. Connect your GitHub in 30 seconds.</p>
          <Link className="btn-primary" to="/signup" data-testid="bh-final-cta">
            Start free — auremcto.com
          </Link>
          <div className="cta-foot">498 of 500 founder spots remaining at $9/month</div>
        </section>

      </div>

      {/* FOOTER */}
      <footer className="footer" data-testid="bh-footer">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <img src="/ora-icon.png" alt="ORA" className="logo-img" />
          <div className="footer-text">ORA by Aurem CTO — Bug Hunt v1 · Iter 212m-73</div>
        </div>
        <div className="footer-links">
          <Link to="/">Home</Link>
          <Link to="/codebase-health">Health Dashboard</Link>
          <Link to="/pricing">Pricing</Link>
          <Link to="/privacy">Privacy</Link>
          <Link to="/terms">Terms</Link>
        </div>
      </footer>
    </div>
  );
}
