/**
 * pages/PolicyPage.jsx — Renders any of /policies/*.md files.
 *
 * Routes:
 *   /privacy          → /policies/privacy-policy.md
 *   /terms            → /policies/terms-of-service.md
 *   /acceptable-use   → /policies/acceptable-use-policy.md
 *
 * Pure static-asset fetch + `marked` for parsing. No backend hop.
 * SEO-friendly: each page has its own URL and ships pre-formatted HTML.
 */
import React, { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { marked } from "marked";

const POLICY_MAP = {
  "privacy":         { file: "privacy-policy.md",       title: "Privacy Policy" },
  "terms":           { file: "terms-of-service.md",     title: "Terms of Service" },
  "acceptable-use":  { file: "acceptable-use-policy.md", title: "Acceptable Use Policy" },
};

export default function PolicyPage({ slug }) {
  // Allow either `slug` prop (when imported with a fixed route) or URL param.
  const params = useParams();
  const key = slug || params.slug || "privacy";
  const meta = POLICY_MAP[key] || POLICY_MAP.privacy;
  const [html, setHtml] = useState("");
  const [err, setErr]   = useState("");

  useEffect(() => {
    fetch(`/policies/${meta.file}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.text();
      })
      .then((md) => setHtml(marked.parse(md)))
      .catch((e) => setErr(String(e.message || e)));
  }, [meta.file]);

  return (
    <div data-testid={`policy-${key}`} style={{
      maxWidth: 820, margin: "0 auto", padding: "40px 24px 80px",
      color: "var(--text, #e8e3d3)",
      fontFamily: "Inter, system-ui, -apple-system, sans-serif",
      lineHeight: 1.65,
    }}>
      <Link to="/" data-testid="back-home" style={{
        fontSize: 12, color: "var(--text-dim)", textDecoration: "none",
      }}>← Back to AUREM CTO</Link>

      <h1 data-testid="policy-title" style={{
        fontSize: 32, fontWeight: 500, letterSpacing: "-0.02em",
        margin: "16px 0 24px",
      }}>{meta.title}</h1>

      {err && (
        <div style={{
          padding: "12px 16px", fontSize: 13,
          background: "rgba(255,107,107,0.06)",
          border: "1px solid rgba(255,107,107,0.2)",
          color: "var(--danger, #ff6b6b)", borderRadius: 5,
        }}>
          Failed to load policy: {err}. Try refreshing or contact{" "}
          <a href="mailto:ora@aurem.live" style={{ color: "inherit" }}>
            ora@aurem.live
          </a>.
        </div>
      )}

      {/* Rendered markdown. Styling via CSS-in-JS class injected into
          page-scoped style tag so we don't pollute global CSS. */}
      <style>{`
        .policy-md h2 { font-size: 20px; font-weight: 500; margin: 28px 0 12px; color: var(--accent-2, #ffc080); }
        .policy-md h3 { font-size: 16px; font-weight: 500; margin: 20px 0 8px; }
        .policy-md p, .policy-md li { font-size: 14px; color: var(--text-dim, #a39d8a); }
        .policy-md strong { color: var(--text, #e8e3d3); font-weight: 600; }
        .policy-md a { color: var(--accent, #ff8a2a); }
        .policy-md table { border-collapse: collapse; width: 100%; margin: 16px 0; }
        .policy-md table th, .policy-md table td { padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border, rgba(255,200,120,0.16)); font-size: 13px; }
        .policy-md table th { color: var(--text); font-weight: 600; }
        .policy-md hr { border: none; border-top: 1px solid var(--border, rgba(255,200,120,0.16)); margin: 28px 0; }
        .policy-md code { background: var(--bg-elev, #0a0c10); padding: 1px 6px; border-radius: 3px; font-size: 12px; }
      `}</style>
      <div
        className="policy-md"
        data-testid="policy-content"
        // eslint-disable-next-line react/no-danger
        dangerouslySetInnerHTML={{ __html: html }}
      />

      <footer style={{
        marginTop: 60, paddingTop: 20,
        borderTop: "1px solid var(--border, rgba(255,200,120,0.16))",
        fontSize: 11, color: "var(--text-faint)",
      }}>
        <Link to="/privacy" style={{ color: "inherit", marginRight: 16 }}>Privacy</Link>
        <Link to="/terms" style={{ color: "inherit", marginRight: 16 }}>Terms</Link>
        <Link to="/acceptable-use" style={{ color: "inherit", marginRight: 16 }}>Acceptable Use</Link>
        <span>· Questions? <a href="mailto:ora@aurem.live" style={{ color: "inherit" }}>ora@aurem.live</a></span>
      </footer>
    </div>
  );
}
