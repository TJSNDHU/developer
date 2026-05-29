/**
 * Landing.jsx — Public hero page.
 */
import React from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Zap, Github, Shield, Code2 } from "lucide-react";
import Shell from "../components/Shell";

export default function Landing() {
  return (
    <Shell>
      <section data-testid="hero" style={{
        minHeight: "82vh",
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
          color: "var(--text)",
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

      <section data-testid="features" style={{ marginTop: 80, maxWidth: 980 }}>
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
            <div key={i} className="card" data-testid={`feature-card-${i}`}>
              <f.Icon size={20} style={{ color: "var(--accent)", marginBottom: 18 }} />
              <span className="eyebrow" style={{ fontSize: 10 }}>{f.tag}</span>
              <p style={{ fontSize: 15, color: "var(--text)", lineHeight: 1.6, marginTop: 10 }}>
                {f.body}
              </p>
            </div>
          ))}
        </div>
      </section>

      <section data-testid="cost-strip" style={{
        marginTop: 60, padding: "20px 0",
        borderTop: "1px solid var(--border)",
        borderBottom: "1px solid var(--border)",
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
    </Shell>
  );
}
