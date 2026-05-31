/**
 * Landing.jsx — Public hero page. No sidebar — minimal floating top-nav
 * + full-bleed background image. Wired separately from the in-app Shell
 * so the marketing surface stays clean.
 */
import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Zap, Github, Shield, Code2 } from "lucide-react";

// Inline blur-up placeholder (~100 bytes) — paints instantly while the
// real WebP downloads. Picture below swaps it in via onLoad.
const BG_PLACEHOLDER =
  "data:image/webp;base64,UklGRlwAAABXRUJQVlA4IFAAAAAQBACdASoYAA0APu1orU2ppqSiMAgBMB2JYgCw7GlgCEHrn3+7cZGzAAD+/Kp19/f5NInbgE9zsLa6db9aIuc6tKDBS0Fot0wMxQVsm/AAAA==";

function useResponsiveBg() {
  const [src, setSrc] = useState(BG_PLACEHOLDER);
  useEffect(() => {
    // Pick mobile (~39 KB) or desktop (~147 KB) by viewport — both massive
    // wins over the 19 MB original. Browser caches forever on next visits.
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
  return (
    <div
      data-testid="landing-root"
      style={{
        minHeight: "100vh",
        position: "relative",
        color: "var(--text)",
        overflow: "hidden",
        // Layer: dark gradient over the live bg (placeholder → real WebP).
        // `transition: background-image` is ignored by browsers, but the
        // hard swap is fine here because the placeholder is already
        // similar in tone — the blur masks the jump.
        background:
          "linear-gradient(180deg, rgba(8,8,12,0.82) 0%, rgba(8,8,12,0.92) 100%), " +
          `url('${bgSrc}') center center / cover no-repeat fixed`,
        // Smooth the placeholder so the 24px-wide tiny blur looks soft
        // until the WebP swaps in.
        filter: bgSrc === BG_PLACEHOLDER ? undefined : undefined,
      }}
    >
      {/* Floating top nav (no sidebar) */}
      <nav
        data-testid="landing-nav"
        style={{
          position: "sticky", top: 0, zIndex: 10,
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "18px clamp(20px, 5vw, 56px)",
          backdropFilter: "blur(8px)",
          background: "rgba(8, 8, 12, 0.45)",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
        }}
      >
        <Link to="/" style={{
          color: "var(--text)", textDecoration: "none",
          fontFamily: "'JetBrains Mono', monospace",
          fontWeight: 600, fontSize: 14, letterSpacing: "0.08em",
        }}>
          AUREM
        </Link>
        <div style={{ display: "flex", gap: 10 }}>
          <Link to="/login" data-testid="nav-login" className="btn-ghost">
            Sign in
          </Link>
          <Link to="/signup" data-testid="nav-signup" className="btn-primary">
            Get started <ArrowRight size={14} />
          </Link>
        </div>
      </nav>

      <main style={{
        maxWidth: 1100,
        margin: "0 auto",
        padding: "clamp(48px, 10vh, 120px) clamp(20px, 5vw, 48px) 80px",
      }}>
        <section data-testid="hero" style={{
          minHeight: "60vh",
          display: "flex", flexDirection: "column",
          justifyContent: "center", alignItems: "flex-start",
          maxWidth: 820,
        }}>
          <div className="eyebrow" style={{ marginBottom: 28 }}>
            <span className="dot" />
            aurem · developers · in public beta
          </div>
          <h1 className="serif" data-testid="hero-headline" style={{
            fontSize: "clamp(38px, 6vw, 64px)",
            lineHeight: 1.05,
            margin: 0,
          }}>
            Build with an{" "}
            <span style={{
              background: "linear-gradient(135deg, var(--accent) 0%, var(--accent-2) 100%)",
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent",
              backgroundClip: "text",
            }}>autonomous CTO</span>.
          </h1>
          <p data-testid="hero-sub" style={{
            fontSize: 18, color: "var(--text-dim)",
            margin: "24px 0 36px", maxWidth: 620, lineHeight: 1.6,
          }}>
            AUREM Dev plans, writes, tests and ships features to your repo.
            1,000 tokens free on signup — no card required.
          </p>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            <Link to="/signup" data-testid="hero-cta-signup" className="btn-primary">
              Claim 1000 tokens <ArrowRight size={16} />
            </Link>
            <Link to="/login" data-testid="hero-cta-login" className="btn-ghost">
              Sign in
            </Link>
          </div>
        </section>

        <section data-testid="features" style={{ marginTop: 80 }}>
          <span className="eyebrow">why developers ship faster</span>
          <h2 className="serif" style={{ fontSize: 30, margin: "12px 0 36px" }}>
            A real teammate. Not a chatbot.
          </h2>
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
            gap: 16,
          }}>
            {[
              { Icon: Zap, tag: "plan → ship",
                body: "Aurem opens a PR with a working feature, plus tests, in minutes." },
              { Icon: Github, tag: "grounded",
                body: "Reads your repo first. Respects existing patterns and file layout." },
              { Icon: Shield, tag: "byok ready",
                body: "Bring your own Anthropic, DeepSeek or Gemini key. Free tokens just remove the setup tax." },
            ].map((f, i) => (
              <div key={i} className="card" data-testid={`feature-card-${i}`} style={{
                background: "rgba(20, 20, 28, 0.55)",
                backdropFilter: "blur(10px)",
                border: "1px solid rgba(255,255,255,0.06)",
              }}>
                <f.Icon size={20} style={{ color: "var(--accent)", marginBottom: 18 }} />
                <span className="eyebrow" style={{ fontSize: 10 }}>{f.tag}</span>
                <p style={{ fontSize: 15, lineHeight: 1.6, marginTop: 10 }}>
                  {f.body}
                </p>
              </div>
            ))}
          </div>
        </section>

        <section data-testid="cost-strip" style={{
          marginTop: 60, padding: "20px 0",
          borderTop: "1px solid rgba(255,255,255,0.08)",
          borderBottom: "1px solid rgba(255,255,255,0.08)",
          display: "flex", flexWrap: "wrap", gap: 28,
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 12, color: "var(--text-faint)",
          letterSpacing: "0.05em", alignItems: "center",
        }}>
          <span>chat = 1 token</span>
          <span>file edit = 2</span>
          <span>test run = 3</span>
          <span>deploy = 5</span>
          <span>fork context = 10</span>
          <Code2 size={14} style={{ color: "var(--accent)", marginLeft: "auto" }} />
        </section>

        <footer style={{
          marginTop: 30, padding: "20px 0 0",
          textAlign: "left", fontSize: 11,
          color: "var(--text-faint)", letterSpacing: "0.05em",
        }}>
          © 2026 AUREM · PIPEDA-compliant · Built for builders
        </footer>
      </main>
    </div>
  );
}
