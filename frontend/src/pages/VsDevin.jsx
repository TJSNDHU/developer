/**
 * VsDevin.jsx — Public comparison page (auremcto.com/vs/devin).
 *
 * SEO + GEO landing page targeting "Devin alternative", "AUREM vs Devin",
 * "AI coding agent comparison" queries.
 *
 * Honesty policy (same as README): June 2026 data, verifiable claims only,
 * we say where Devin wins. Devin pricing verified June 2026 against
 * devin.ai/pricing + third-party sources — re-verify quarterly.
 *
 * Sections:
 *   1. Hero            — claim + last-verified date
 *   2. Verdict cards   — "Choose AUREM if / Choose Devin if"
 *   3. Pricing reality — flat fee vs ACU metering, worked example
 *   4. Feature table   — verifiable feature matrix
 *   5. Where Devin wins— honest credit
 *   6. FAQ             — mirrors FAQPage JSON-LD injected below
 *   7. CTA             — free tier, no card
 *
 * No fake testimonials. No benchmark claims we can't reproduce.
 */
import React, { useEffect } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight, Check, X, Minus, Shield, Brain, Zap,
  GitCommitHorizontal, Smartphone, DollarSign,
} from "lucide-react";

/* ── SEO constants ─────────────────────────────────────────────── */
const TITLE =
  "AUREM CTO vs Devin (2026) — honest comparison | Devin alternative";
const DESCRIPTION =
  "AUREM CTO vs Devin, compared honestly: flat $9–$49/mo vs $20 + $2.25/ACU " +
  "metered billing, free tier vs none, direct GitHub commit or PR, 25+ pattern " +
  "pre-commit security scan, two-model Maxx review. June 2026 data.";
const CANONICAL = "https://auremcto.com/vs/devin";
const LAST_VERIFIED = "June 2026";

const FAQ = [
  {
    q: "Is AUREM CTO a good Devin alternative?",
    a: "Yes, if you want predictable cost and shipped commits. AUREM CTO is " +
       "an autonomous AI engineer with flat pricing ($0 free tier, $9 Starter, " +
       "$19 Pro unlimited tasks, $49 Team) instead of Devin's $20/month plus " +
       "$2.25 per Agent Compute Unit metering. AUREM also runs a 25+ pattern " +
       "security scan and an optional second-model code review (Maxx mode) " +
       "before anything reaches your repository.",
  },
  {
    q: "How is AUREM CTO's pricing different from Devin's?",
    a: "Devin bills by Agent Compute Units: the $20/month Core plan charges " +
       "$2.25 per ACU on top, and the $500/month Team plan includes 250 ACUs. " +
       "Moderate Core usage typically lands around $70–220/month in total. " +
       "AUREM CTO is a flat fee: Pro is $19/month with unlimited tasks — " +
       "5 tasks or 500, the price is the same.",
  },
  {
    q: "Does AUREM CTO commit directly to GitHub like Devin opens PRs?",
    a: "AUREM CTO supports two delivery modes: direct commit to your branch " +
       "via the GitHub REST API for solo speed, or a Pull Request flow when " +
       "your team prefers review. Devin delivers its work as pull requests.",
  },
  {
    q: "Which is safer — AUREM CTO or Devin?",
    a: "AUREM CTO runs the Vanguard scanner on every change before it reaches " +
       "GitHub: 25+ patterns covering leaked secrets (AWS, GitHub, Stripe, " +
       "OpenAI keys, DB connection strings) and dangerous code (eval, " +
       "shell=True, SQL string formatting, unsafe innerHTML), plus Python AST " +
       "and esbuild syntax validation. On Pro and Team, Maxx mode adds a " +
       "second-model review by Claude Sonnet before commit. Devin does not " +
       "advertise an equivalent pre-commit security gate.",
  },
  {
    q: "When is Devin the better choice?",
    a: "Devin is a strong fit for long-running, fully cloud-hosted sessions " +
       "in its own VM, organisations that want VPC deployment, and teams " +
       "already standardised on PR-only review with ACU budgets. If those " +
       "matter more to you than flat pricing, a free tier, mobile access, or " +
       "pre-commit security scanning, choose Devin.",
  },
];

/* ── Feature matrix (June 2026, verifiable only) ───────────────── */
const ROWS = [
  ["Pricing model", "Flat fee — $0 / $9 / $19 / $49 per month", "ACU-metered — $20/mo + $2.25/ACU (Core), $500/mo incl. 250 ACUs (Team)"],
  ["Free tier", "10 tasks/month, no credit card", "None"],
  ["Delivery mode", "Direct commit to branch or Pull Request — your choice", "Pull Request"],
  ["Per-repo memory", "Project Brain — stack, decisions, preferences, history", "Devin Wiki / knowledge"],
  ["Pre-commit security scan", "Vanguard — 25+ secret & dangerous-code patterns, AST + esbuild checks", "Not advertised"],
  ["Two-model review", "Maxx mode — DeepSeek V3 writes, Claude Sonnet reviews", "Single agent"],
  ["Parallel agents", "3 — backend / frontend / tests", "Multiple cloud sessions"],
  ["Works without an IDE", "Web UI + mobile + VS Code extension", "Web UI (desktop), Slack, IDE beta"],
  ["Browser error capture", "F12 one-line script auto-routes errors to debug", "In-VM Chromium browsing"],
  ["Webhook automations", "GitHub push → auto-task templates", "API available"],
  ["Cloud VM / VPC deployment", "Not offered", "Yes — SaaS or VPC (Enterprise)"],
  ["Long unattended sessions", "Minutes-scale tasks", "Hours-scale autonomous sessions"],
];

/* ── tiny presentational helpers ───────────────────────────────── */
const S = {
  page:   { minHeight: "100vh", background: "var(--bg)", color: "var(--text)",
            fontFamily: "inherit" },
  shell:  { maxWidth: 1080, margin: "0 auto", padding: "0 20px" },
  h2:     { fontSize: 26, fontWeight: 600, margin: "64px 0 16px" },
  dim:    { color: "var(--text-dim)", lineHeight: 1.65 },
  card:   { background: "var(--panel)", border: "1px solid var(--border)",
            borderRadius: 14, padding: "22px 24px" },
  badge:  { display: "inline-flex", alignItems: "center", gap: 6,
            fontSize: 12, color: "var(--text-dim)",
            border: "1px solid var(--border)", borderRadius: 999,
            padding: "4px 12px" },
  cta:    { display: "inline-flex", alignItems: "center", gap: 8,
            background: "var(--accent)", color: "#1a0e00", fontWeight: 600,
            borderRadius: 10, padding: "12px 22px", textDecoration: "none" },
  ghost:  { display: "inline-flex", alignItems: "center", gap: 8,
            border: "1px solid var(--border-strong)", color: "var(--text)",
            borderRadius: 10, padding: "12px 22px", textDecoration: "none" },
};

function MetaTags() {
  useEffect(() => {
    document.title = TITLE;

    const ensure = (selector, create) => {
      let el = document.head.querySelector(selector);
      if (!el) { el = create(); document.head.appendChild(el); }
      return el;
    };

    const desc = ensure('meta[name="description"]', () => {
      const m = document.createElement("meta");
      m.setAttribute("name", "description");
      return m;
    });
    const prevDesc = desc.getAttribute("content");
    desc.setAttribute("content", DESCRIPTION);

    const canon = ensure('link[rel="canonical"]', () => {
      const l = document.createElement("link");
      l.setAttribute("rel", "canonical");
      return l;
    });
    const prevCanon = canon.getAttribute("href");
    canon.setAttribute("href", CANONICAL);

    const ld = document.createElement("script");
    ld.type = "application/ld+json";
    ld.id = "ld-vs-devin";
    ld.text = JSON.stringify({
      "@context": "https://schema.org",
      "@graph": [
        {
          "@type": "FAQPage",
          mainEntity: FAQ.map(({ q, a }) => ({
            "@type": "Question",
            name: q,
            acceptedAnswer: { "@type": "Answer", text: a },
          })),
        },
        {
          "@type": "BreadcrumbList",
          itemListElement: [
            { "@type": "ListItem", position: 1, name: "AUREM CTO",
              item: "https://auremcto.com/" },
            { "@type": "ListItem", position: 2, name: "AUREM CTO vs Devin",
              item: CANONICAL },
          ],
        },
      ],
    });
    document.head.appendChild(ld);

    return () => {
      if (prevDesc != null) desc.setAttribute("content", prevDesc);
      if (prevCanon != null) canon.setAttribute("href", prevCanon);
      document.getElementById("ld-vs-devin")?.remove();
    };
  }, []);
  return null;
}

/* ── page ──────────────────────────────────────────────────────── */
export default function VsDevin() {
  return (
    <div data-testid="vs-devin-root" style={S.page}>
      <MetaTags />

      <div style={S.shell}>
        {/* 1 ── Hero */}
        <header style={{ padding: "72px 0 8px", textAlign: "center" }}>
          <span style={S.badge} data-testid="vs-devin-verified">
            Honest comparison · data verified {LAST_VERIFIED} · no benchmarks we can&apos;t reproduce
          </span>
          <h1 style={{ fontSize: 40, fontWeight: 700, margin: "20px 0 14px",
                       lineHeight: 1.15 }}>
            AUREM CTO vs Devin
          </h1>
          <p style={{ ...S.dim, maxWidth: 640, margin: "0 auto", fontSize: 17 }}>
            Both are autonomous AI software engineers: you describe the task,
            they read the codebase, write the code, and deliver it to GitHub.
            The differences are how the work is priced, how it lands in your
            repo, and what checks run before it gets there.
          </p>
        </header>

        {/* 2 ── Verdict cards */}
        <section style={{ display: "grid", gap: 16, marginTop: 40,
                          gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))" }}>
          <div style={{ ...S.card, borderColor: "var(--border-strong)" }}
               data-testid="vs-devin-pick-aurem">
            <h2 style={{ fontSize: 18, fontWeight: 600, margin: "0 0 10px" }}>
              Choose AUREM CTO if you want…
            </h2>
            <ul style={{ ...S.dim, margin: 0, paddingLeft: 18, lineHeight: 1.9 }}>
              <li>A predictable flat bill — $19/mo Pro, unlimited tasks</li>
              <li>A real free tier (10 tasks/month, no card)</li>
              <li>Direct commit to your branch <em>or</em> a PR — your call</li>
              <li>A 25+ pattern security scan before anything lands</li>
              <li>Maxx mode: a second model reviewing every critical change</li>
              <li>To ship from your phone or any browser</li>
            </ul>
          </div>
          <div style={S.card} data-testid="vs-devin-pick-devin">
            <h2 style={{ fontSize: 18, fontWeight: 600, margin: "0 0 10px" }}>
              Choose Devin if you want…
            </h2>
            <ul style={{ ...S.dim, margin: 0, paddingLeft: 18, lineHeight: 1.9 }}>
              <li>Hours-long unattended sessions in a cloud VM</li>
              <li>VPC deployment inside your own cloud (Enterprise)</li>
              <li>A strictly PR-only review workflow</li>
              <li>In-VM browser automation for end-to-end checks</li>
              <li>An ACU budget model your finance team already approved</li>
            </ul>
          </div>
        </section>

        {/* 3 ── Pricing reality */}
        <section>
          <h2 style={S.h2}>
            <DollarSign size={22} style={{ verticalAlign: -3, marginRight: 8,
                                           color: "var(--accent)" }} aria-hidden />
            The pricing difference, in real numbers
          </h2>
          <p style={{ ...S.dim, maxWidth: 760 }}>
            Devin meters work in Agent Compute Units. The $20/month Core plan
            bills $2.25 per ACU on top of the subscription, and an ACU covers
            roughly 15 minutes of active agent work — third-party estimates put
            moderate Core usage at about $70–220 per month all-in. The
            $500/month Team plan includes 250 ACUs, then $2.00 per additional
            ACU. AUREM CTO is a flat fee: Free is 10 tasks/month, Starter is
            $9 for 50 tasks, Pro is $19 with unlimited tasks, Team is $49 per
            user. Ship 5 tasks or 500 — the invoice doesn&apos;t move.
          </p>
          <p style={{ ...S.dim, fontSize: 13 }}>
            Devin figures last verified {LAST_VERIFIED} from devin.ai/pricing
            and independent pricing trackers. Always check the vendor page —
            prices change.
          </p>
        </section>

        {/* 4 ── Feature matrix */}
        <section>
          <h2 style={S.h2}>Feature by feature</h2>
          <div style={{ overflowX: "auto", border: "1px solid var(--border)",
                        borderRadius: 14 }}>
            <table data-testid="vs-devin-table"
                   style={{ width: "100%", borderCollapse: "collapse",
                            fontSize: 14, minWidth: 640 }}>
              <thead>
                <tr style={{ background: "var(--panel-2)", textAlign: "left" }}>
                  <th style={{ padding: "12px 16px", fontWeight: 600 }}> </th>
                  <th style={{ padding: "12px 16px", fontWeight: 600,
                               color: "var(--accent-2)" }}>AUREM CTO</th>
                  <th style={{ padding: "12px 16px", fontWeight: 600 }}>Devin</th>
                </tr>
              </thead>
              <tbody>
                {ROWS.map(([label, aurem, devin]) => (
                  <tr key={label}
                      style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ padding: "12px 16px", color: "var(--text-dim)",
                                 whiteSpace: "nowrap" }}>{label}</td>
                    <td style={{ padding: "12px 16px" }}>{aurem}</td>
                    <td style={{ padding: "12px 16px",
                                 color: "var(--text-dim)" }}>{devin}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p style={{ ...S.dim, fontSize: 13, marginTop: 10 }}>
            Sources: AUREM CTO production codebase and docs; Devin public
            pricing and documentation, {LAST_VERIFIED}. Spotted something
            stale? Email ora@aurem.live and we&apos;ll fix it.
          </p>
        </section>

        {/* 5 ── What makes AUREM different, concretely */}
        <section>
          <h2 style={S.h2}>What &ldquo;safer by default&rdquo; means here</h2>
          <div style={{ display: "grid", gap: 16,
                        gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
            {[
              [Shield, "Vanguard pre-commit scan",
               "25+ patterns checked on every change: AWS / GitHub / Stripe / " +
               "OpenAI keys, DB connection strings, private keys, plus eval, " +
               "shell=True, SQL string formatting and unsafe innerHTML. " +
               "Critical hits block the commit."],
              [Zap, "Maxx two-model review",
               "DeepSeek V3 writes, Claude Sonnet reviews — wrong imports, " +
               "logic errors and security gaps get caught before commit. " +
               "100 reviews/mo on Pro, unlimited on Team."],
              [GitCommitHorizontal, "Two delivery modes",
               "Direct commit via the GitHub REST API when speed matters, " +
               "or a Pull Request when your team wants eyes on the diff."],
              [Brain, "Project Brain",
               "Per-repo permanent memory: your stack, your conventions, " +
               "your past decisions. Say \"we don't use Redux\" once."],
              [Smartphone, "Ships from anywhere",
               "Full web UI that works on mobile, plus a VS Code extension. " +
               "No desktop IDE required."],
            ].map(([Icon, title, body]) => (
              <div key={title} style={S.card}>
                <Icon size={20} style={{ color: "var(--accent)" }} aria-hidden />
                <h3 style={{ fontSize: 15, fontWeight: 600, margin: "10px 0 6px" }}>
                  {title}
                </h3>
                <p style={{ ...S.dim, fontSize: 13.5, margin: 0 }}>{body}</p>
              </div>
            ))}
          </div>
        </section>

        {/* 6 ── FAQ (mirrors JSON-LD) */}
        <section>
          <h2 style={S.h2}>Frequently asked</h2>
          <div style={{ display: "grid", gap: 12 }}>
            {FAQ.map(({ q, a }) => (
              <details key={q} style={{ ...S.card, padding: "16px 20px" }}>
                <summary style={{ cursor: "pointer", fontWeight: 600,
                                  fontSize: 15 }}>{q}</summary>
                <p style={{ ...S.dim, fontSize: 14, margin: "10px 0 0" }}>{a}</p>
              </details>
            ))}
          </div>
        </section>

        {/* 7 ── CTA */}
        <section style={{ textAlign: "center", padding: "72px 0 88px" }}>
          <h2 style={{ fontSize: 28, fontWeight: 700, margin: "0 0 12px" }}>
            Try the comparison yourself
          </h2>
          <p style={{ ...S.dim, maxWidth: 520, margin: "0 auto 26px" }}>
            10 free tasks every month. No credit card. Connect a repo and ORA
            ships its first commit in about two minutes.
          </p>
          <div style={{ display: "flex", gap: 14, justifyContent: "center",
                        flexWrap: "wrap" }}>
            <Link to="/signup" style={S.cta} data-testid="vs-devin-cta">
              Start free <ArrowRight size={18} aria-hidden />
            </Link>
            <Link to="/wall" style={S.ghost}>
              See real ships on the Wall
            </Link>
          </div>
        </section>
      </div>
    </div>
  );
}
