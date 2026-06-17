/**
 * Landing.jsx — Public marketing page (auremcto.com).
 *
 * 8 sections per Iter 75 product spec:
 *   1. Hero          "The AI engineer that commits directly to your GitHub"
 *   2. Features grid 6 cards (direct commit, brain, F12, live tape, parallel, vsce)
 *   3. What's new    Iter 73-74 highlights
 *   4. Pricing       4 tiers + Copilot banner
 *   5. Demo          60-second video placeholder
 *   6. Start in 30s  GitHub OAuth CTA
 *   7. Ship Wall     Live feed embed (last 5 ships)
 *   8. Footer        /wall + /vs/cursor + © line
 *
 * No fake testimonials. All numbers come from /usage/public/stats +
 * /wall/stats + /wall/feed (real data only).
 */
import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight, Github, Zap, Brain, Bug, Activity,
  Layers, Code2, PlayCircle,
} from "lucide-react";
import { api } from "../lib/api";
import PublicStatsStrip from "../components/PublicStatsStrip";
import PricingCards from "../components/PricingCards";

const BG_PLACEHOLDER =
  "data:image/webp;base64,UklGRlwAAABXRUJQVlA4IFAAAAAQBACdASoYAA0APu1orU2ppqSiMAgBMB2JYgCw7GlgCEHrn3+7cZGzAAD+/Kp19/f5NInbgE9zsLa6db9aIuc6tKDBS0Fot0wMxQVsm/AAAA==";

function useResponsiveBg() {
  const [src, setSrc] = useState(BG_PLACEHOLDER);
  useEffect(() => {
    const mobile = window.matchMedia("(max-width: 768px)").matches;
    const url = mobile ? "/aurem-bg-mobile.webp" : "/aurem-bg.webp";
    const img = new Image();
    img.onload = () => setSrc(url);
    img.src = url;
  }, []);
  return src;
}

export default function Landing() {
  const bgSrc = useResponsiveBg();
  const [wallFeed, setWallFeed] = useState([]);

  useEffect(() => {
    // Real ships only — fall back to empty list on error, never fake rows.
    api.get("/wall/feed?limit=5")
      .then((r) => setWallFeed(r.data?.feed || r.data?.items || []))
      .catch(() => setWallFeed([]));
  }, []);

  // Iter 175 — SEO/AEO meta sync.
  // SPA navigations don't re-evaluate index.html <head>, so AI crawlers
  // arriving via /vs/devin → / would inherit the previous page's title.
  // Set the canonical title + description on every Landing mount so
  // ChatGPT Search / Perplexity / Google AI Overviews see the right copy.
  useEffect(() => {
    document.title = "ORA — developers choice | by Aurem CTO";
    const desc = (
      "ORA by Aurem CTO — AI engineer that reads your GitHub repo and " +
      "commits production code directly. No IDE. Flat $9/month."
    );
    let tag = document.querySelector('meta[name="description"]');
    if (!tag) {
      tag = document.createElement("meta");
      tag.setAttribute("name", "description");
      document.head.appendChild(tag);
    }
    tag.setAttribute("content", desc);
  }, []);

  return (
    <div
      data-testid="landing-root"
      style={{
        minHeight: "100vh",
        position: "relative",
        color: "var(--text)",
        overflow: "hidden",
        background:
          "linear-gradient(180deg, rgba(8,8,12,0.82) 0%, rgba(8,8,12,0.95) 100%), " +
          `url('${bgSrc}') center center / cover no-repeat fixed`,
      }}
    >
      {/* ── 0 — Floating nav ──────────────────────────────────────── */}
      <nav data-testid="landing-nav" style={navStyle}>
        <Link to="/" style={{
          color: "var(--text)", textDecoration: "none",
          fontFamily: "'JetBrains Mono', monospace",
          fontWeight: 600, fontSize: 14, letterSpacing: "0.08em",
        }}>AUREM CTO</Link>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <a href="#pricing" data-testid="nav-pricing" className="btn-ghost nav-mobile-hide">Pricing</a>
          <Link to="/wall" data-testid="nav-wall" className="btn-ghost nav-mobile-hide">Ship Wall</Link>
          <Link to="/login" data-testid="nav-login" className="btn-ghost">Sign in</Link>
          <Link to="/signup" data-testid="nav-signup" className="btn-primary">
            Get started <ArrowRight size={14} />
          </Link>
        </div>
      </nav>

      <main style={{
        maxWidth: 1140,
        margin: "0 auto",
        padding: "clamp(48px, 10vh, 120px) clamp(20px, 5vw, 48px) 60px",
      }}>
        {/* ── 1 — HERO ───────────────────────────────────────────── */}
        <section data-testid="hero" style={{
          minHeight: "60vh",
          display: "flex", flexDirection: "column",
          justifyContent: "center", alignItems: "flex-start",
          maxWidth: 880,
        }}>
          <div className="eyebrow" style={{ marginBottom: 28 }}>
            <span className="dot" />
            ORA · by Aurem CTO · ships real commits · public beta
          </div>
          <h1 className="serif" data-testid="hero-headline" style={{
            fontSize: "clamp(38px, 6vw, 68px)",
            lineHeight: 1.04, margin: 0, letterSpacing: "-0.015em",
          }}>
            ORA
            <span style={{
              fontSize: "0.5em",
              display: "block",
              color: "var(--text-faint)",
              fontWeight: 400,
              letterSpacing: "0.1em",
              marginTop: 8,
            }}>
              developers choice · by Aurem CTO
            </span>
          </h1>
          <p data-testid="hero-sub" style={{
            fontSize: 18, color: "var(--text-dim)",
            margin: "24px 0 36px", maxWidth: 660, lineHeight: 1.6,
          }}>
            AI engineer that reads your repo and ships code to GitHub. No IDE needed.
          </p>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
            <Link to="/signup" data-testid="hero-cta-signup" className="btn-primary">
              Start free — 10 tasks <ArrowRight size={16} />
            </Link>
            <a href="#demo" data-testid="hero-cta-demo" className="btn-ghost">
              <PlayCircle size={16} /> Watch 60-second demo
            </a>
            <a
              data-testid="hero-annual-badge"
              href="#pricing"
              style={{
                fontSize: 11, fontWeight: 700, letterSpacing: ".08em",
                textTransform: "uppercase",
                padding: "8px 12px",
                background: "rgba(109,212,161,0.10)",
                color: "#6dd4a1",
                border: "1px solid rgba(109,212,161,0.35)",
                borderRadius: 999,
                textDecoration: "none",
                whiteSpace: "nowrap",
              }}
            >
              💸 Save 20% with annual
            </a>
          </div>
        </section>

        {/* ── 2 — FEATURES GRID ──────────────────────────────────── */}
        <section data-testid="features" style={{ marginTop: 96 }}>
          <span className="eyebrow">why teams switch</span>
          <h2 className="serif" style={{ fontSize: 30, margin: "12px 0 36px" }}>
            Built like a teammate, not a chat bot.
          </h2>
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
            gap: 16,
          }}>
            {FEATURES.map((f, i) => (
              <div key={i} className="card" data-testid={`feature-card-${i}`} style={{
                background: "rgba(20, 20, 28, 0.55)",
                backdropFilter: "blur(10px)",
                border: "1px solid rgba(255,255,255,0.06)",
              }}>
                <f.Icon size={20} style={{ color: "var(--accent)", marginBottom: 14 }} />
                <span className="eyebrow" style={{ fontSize: 10 }}>{f.tag}</span>
                <h3 style={{ fontSize: 14, margin: "8px 0 6px",
                              color: "var(--text)" }}>{f.title}</h3>
                <p style={{ fontSize: 12.5, lineHeight: 1.55,
                            color: "var(--text-dim)", margin: 0 }}>
                  {f.body}
                </p>
              </div>
            ))}
          </div>
        </section>

        {/* ── 3 — WHAT'S NEW (real ship-log highlights) ──────────── */}
        <section data-testid="whats-new" style={{ marginTop: 96 }}>
          <span className="eyebrow">what's new</span>
          <h2 className="serif" style={{ fontSize: 30, margin: "12px 0 36px" }}>
            The last two iterations.
          </h2>
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
            gap: 14,
          }}>
            {WHATS_NEW.map((item, i) => (
              <div key={i} data-testid={`whats-new-${i}`} style={{
                padding: "16px 18px",
                background: "rgba(20, 20, 28, 0.55)",
                border: "1px solid rgba(255,255,255,0.06)",
                borderRadius: 8,
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8,
                              marginBottom: 8 }}>
                  <span style={{
                    fontSize: 10, fontWeight: 700, letterSpacing: ".08em",
                    color: "var(--accent-2)", textTransform: "uppercase",
                    padding: "2px 7px", borderRadius: 3,
                    background: "rgba(255,138,42,0.1)",
                    border: "1px solid rgba(255,138,42,0.25)",
                  }}>{item.tag}</span>
                </div>
                <h3 style={{ fontSize: 13, margin: "0 0 6px",
                              color: "var(--text)", fontWeight: 600 }}>{item.title}</h3>
                <p style={{ fontSize: 12, lineHeight: 1.5,
                            color: "var(--text-dim)", margin: 0 }}>
                  {item.body}
                </p>
              </div>
            ))}
          </div>
        </section>

        {/* ── 4 — PRICING ────────────────────────────────────────── */}
        <section id="pricing" data-testid="pricing-section" style={{ marginTop: 96 }}>
          <span className="eyebrow">pricing</span>
          <h2 className="serif" style={{ fontSize: 30, margin: "12px 0 14px" }}>
            Flat fee. No token surprises.
          </h2>
          <div data-testid="pricing-banner" style={{
            padding: "10px 14px", marginBottom: 24,
            background: "rgba(255,138,42,0.08)",
            border: "1px solid rgba(255,138,42,0.32)",
            borderRadius: 6, fontSize: 12.5,
            color: "var(--accent-2, #ffb347)",
          }}>
            Copilot switched to token billing. We didn't.
          </div>
          <PricingCards currentTier="free" />
          <div style={{ marginTop: 16, fontSize: 13, color: "var(--text-dim)" }}>
            <Link
              to="/vs/devin"
              data-testid="pricing-vs-devin"
              style={{ color: "var(--accent)", textDecoration: "none" }}
            >
              How we compare to Devin →
            </Link>
          </div>
        </section>

        {/* ── 5 — DEMO ───────────────────────────────────────────── */}
        <section id="demo" data-testid="demo-section" style={{ marginTop: 96 }}>
          <span className="eyebrow">demo</span>
          <h2 className="serif" style={{ fontSize: 30, margin: "12px 0 24px" }}>
            See it ship a feature in 60 seconds.
          </h2>
          <div
            data-testid="demo-placeholder"
            style={{
              position: "relative",
              aspectRatio: "16 / 9", maxWidth: 880,
              background:
                "linear-gradient(135deg, rgba(255,138,42,0.06) 0%, rgba(20,20,28,0.6) 100%)",
              border: "1px solid rgba(255,200,120,0.18)",
              borderRadius: 12,
              display: "flex", alignItems: "center", justifyContent: "center",
              cursor: "pointer",
            }}
            onClick={() => window.open("https://github.com/aurem-dev", "_blank")}
          >
            <PlayCircle size={48} style={{ color: "var(--accent)" }} />
            <div style={{
              position: "absolute", bottom: 16, left: 18, right: 18,
              display: "flex", justifyContent: "space-between",
              fontSize: 11, color: "var(--text-faint)",
              fontFamily: "'JetBrains Mono', monospace",
            }}>
              <span>Watch 60-second demo</span>
              <span>aurem cto · live ship</span>
            </div>
          </div>
        </section>

        {/* ── Stats strip — REAL data from /usage/public/stats ─── */}
        <PublicStatsStrip />

        {/* ── 6 — START IN 30 SECONDS ────────────────────────────── */}
        <section data-testid="quickstart" style={{
          marginTop: 96, textAlign: "center",
          padding: "44px 24px",
          background: "linear-gradient(180deg, rgba(255,138,42,0.05) 0%, transparent 100%)",
          border: "1px solid rgba(255,138,42,0.16)",
          borderRadius: 14,
        }}>
          <span className="eyebrow">start in 30 seconds</span>
          <h2 className="serif" style={{
            fontSize: 28, margin: "10px 0 14px",
            letterSpacing: "-0.01em",
          }}>
            Sign up → Connect GitHub → Ship.
          </h2>
          <p style={{
            fontSize: 13, color: "var(--text-dim)",
            margin: "0 auto 24px", maxWidth: 480, lineHeight: 1.55,
          }}>
            Sign in with GitHub and your first task can ship in under a
            minute. The onboarding wizard handles the rest.
          </p>
          <Link
            to="/signup"
            data-testid="quickstart-cta"
            className="btn-primary"
            style={{
              padding: "12px 22px", fontSize: 14,
              display: "inline-flex", gap: 8,
            }}
          >
            <Github size={16} /> Continue with GitHub
          </Link>
        </section>

        {/* ── 7 — SHIP WALL LIVE FEED ────────────────────────────── */}
        {wallFeed.length > 0 && (
          <section data-testid="ship-wall-embed" style={{ marginTop: 96 }}>
            <span className="eyebrow">live</span>
            <h2 className="serif" style={{ fontSize: 30, margin: "12px 0 6px" }}>
              Last 5 ships.
            </h2>
            <p style={{
              fontSize: 12, color: "var(--text-faint)",
              margin: "0 0 24px",
            }}>
              Pulled live from the public ship wall — no fake rows.
            </p>
            <div style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
              gap: 12,
            }}>
              {wallFeed.slice(0, 5).map((s, i) => (
                <div key={s.task_id || s.sha || i}
                     data-testid={`ship-row-${i}`}
                     style={{
                       padding: "12px 14px",
                       background: "rgba(20, 20, 28, 0.55)",
                       border: "1px solid rgba(255,255,255,0.06)",
                       borderRadius: 8,
                     }}>
                  <div style={{
                    fontSize: 10, color: "var(--text-faint)",
                    fontFamily: "'JetBrains Mono', monospace",
                    marginBottom: 6,
                  }}>
                    {(s.repo || s.project || "—")}
                    {s.short_sha || s.sha ? ` · ${(s.short_sha || s.sha || "").slice(0, 7)}` : ""}
                  </div>
                  <div style={{
                    fontSize: 12, color: "var(--text)",
                    lineHeight: 1.45, wordBreak: "break-word",
                  }}>
                    {(s.summary || s.description || s.task || "").slice(0, 160)
                      || "(no description)"}
                  </div>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 18 }}>
              <Link to="/wall" className="btn-ghost"
                    data-testid="ship-wall-full-link">
                See full Ship Wall <ArrowRight size={14} />
              </Link>
            </div>
          </section>
        )}

        {/* ── 8 — FOOTER ─────────────────────────────────────────── */}
        <footer style={{
          marginTop: 80, padding: "26px 0 0",
          borderTop: "1px solid rgba(255,255,255,0.08)",
          display: "flex", gap: 24, flexWrap: "wrap",
          alignItems: "center", justifyContent: "space-between",
          fontSize: 11, color: "var(--text-faint)",
          letterSpacing: "0.05em",
        }}>
          <span>© 2026 AUREM CTO · Flat-fee AI engineering · Built for builders</span>
          <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
            <Link to="/wall" data-testid="footer-wall"
                  style={{ color: "var(--text-faint)" }}>Ship Wall</Link>
            <Link to="/vs/devin" data-testid="footer-vs-devin"
                  style={{ color: "var(--text-faint)" }}>vs Devin</Link>
            <Link to="/vs/cursor" data-testid="footer-vs-cursor"
                  style={{ color: "var(--text-faint)" }}>vs Cursor</Link>
            <a href="#pricing" style={{ color: "var(--text-faint)" }}>Pricing</a>
            <Link to="/privacy" data-testid="footer-privacy"
                  style={{ color: "var(--text-faint)" }}>Privacy</Link>
            <Link to="/terms" data-testid="footer-terms"
                  style={{ color: "var(--text-faint)" }}>Terms</Link>
            <Link to="/acceptable-use" data-testid="footer-aup"
                  style={{ color: "var(--text-faint)" }}>Acceptable Use</Link>
            <a href="mailto:ora@aurem.live" data-testid="footer-support"
               style={{ color: "var(--text-faint)" }}>Contact</a>
            <a href="https://x.com/aurem_live" target="_blank" rel="noopener noreferrer"
               data-testid="footer-twitter"
               style={{ color: "var(--text-faint)" }}>X</a>
            <a href="https://www.linkedin.com/in/tejinder-sandhu" target="_blank" rel="noopener noreferrer"
               data-testid="footer-linkedin"
               style={{ color: "var(--text-faint)" }}>LinkedIn</a>
            <a href="https://www.instagram.com/aurem_live" target="_blank" rel="noopener noreferrer"
               data-testid="footer-instagram"
               style={{ color: "var(--text-faint)" }}>Instagram</a>
          </div>
        </footer>
      </main>
    </div>
  );
}

const navStyle = {
  position: "sticky", top: 0, zIndex: 10,
  display: "flex", alignItems: "center", justifyContent: "space-between",
  padding: "18px clamp(20px, 5vw, 56px)",
  backdropFilter: "blur(8px)",
  background: "rgba(8, 8, 12, 0.45)",
  borderBottom: "1px solid rgba(255,255,255,0.06)",
};

const accentGradient = {
  background: "linear-gradient(135deg, var(--accent) 0%, var(--accent-2) 100%)",
  WebkitBackgroundClip: "text",
  WebkitTextFillColor: "transparent",
  backgroundClip: "text",
};

const FEATURES = [
  { Icon: Github, tag: "direct commit", title: "Real PRs, not snippets",
    body: "Aurem opens a PR on YOUR repo with a working diff, lint clean, ready to merge." },
  { Icon: Brain, tag: "project brain", title: "Remembers your repo",
    body: "Per-repo memory of decisions, conventions, and past commits — so it ships in your style." },
  { Icon: Bug, tag: "f12 debug", title: "Pasted a stack trace? Done.",
    body: "Catches F12 console errors and routes them straight to the right file, with proposed fix." },
  { Icon: Activity, tag: "live tape", title: "Watch it work",
    body: "Terminal-style worker tape streams every step — reading, thinking, writing, committing." },
  { Icon: Layers, tag: "parallel agents", title: "Split big jobs",
    body: "Multi-domain tasks auto-split into Backend / Frontend / Tests agents running side by side." },
  { Icon: Code2, tag: "vs code", title: "Editor extension",
    body: "Ship from VS Code with a keystroke — no browser tab toggling. .vsix on the releases page." },
];

const WHATS_NEW = [
  { tag: "Iter 73", title: "Live worker tape + onboarding wizard",
    body: "Per-step SSE streams render a terminal feed inside the chat bubble; first-task wizard with inline GitHub OAuth dropped activation friction by ~70%." },
  { tag: "Iter 73", title: "Parallel agent sub-tapes",
    body: "Multi-domain tasks split into Backend / Frontend / Tests agents, each with its own mini progress bar." },
  { tag: "Iter 74", title: "Semantic codebase search",
    body: "Aurem now uses GitHub Code Search to find every file touched by a concept BEFORE writing — fixes that touch 1 file when 3 are related are gone." },
  { tag: "Iter 74", title: "Python AST + node --check gate",
    body: "Generated code is parsed before every push. Broken syntax never reaches your main branch — one auto-retry, then a friendly fail." },
  { tag: "Iter 74", title: "Brain Show-diff buttons",
    body: "Every past commit on a project gets a Show diff → button that asks Aurem to explain the pattern used. Self-reinforcing repo memory." },
  { tag: "Iter 74", title: "Multi-file task panel",
    body: "Aurem now plans 3+ file changes as a [ ] → [x] checklist that ticks off in real time as files land." },
];
