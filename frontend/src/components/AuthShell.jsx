/**
 * AuthShell.jsx — Bg-bleed wrapper used by /login and /signup (and any other
 * pre-auth surface). Mirrors `pages/Landing.jsx`:
 *   • Floating top-nav (logo + minimal action)
 *   • Same responsive WebP background image (instant blur placeholder → real)
 *   • Same dark gradient overlay so copy stays readable
 *
 * Deliberately does NOT use `<Shell>` (which renders the in-app sidebar).
 */
import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

// 100-byte inline blur placeholder — identical to Landing.jsx so the
// browser cache hits across pages.
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

export default function AuthShell({ children, secondaryCta }) {
  const bgSrc = useResponsiveBg();
  return (
    <div
      data-testid="auth-shell"
      style={{
        minHeight: "100vh",
        position: "relative",
        color: "var(--text)",
        overflow: "hidden",
        background:
          "linear-gradient(180deg, rgba(8,8,12,0.82) 0%, rgba(8,8,12,0.92) 100%), " +
          `url('${bgSrc}') center center / cover no-repeat fixed`,
      }}
    >
      {/* Floating top nav — same treatment as Landing */}
      <nav
        data-testid="auth-nav"
        style={{
          position: "sticky", top: 0, zIndex: 10,
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "18px clamp(20px, 5vw, 56px)",
          backdropFilter: "blur(8px)",
          background: "rgba(8, 8, 12, 0.45)",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
        }}
      >
        <Link to="/" data-testid="auth-logo" style={{
          color: "var(--text)", textDecoration: "none",
          fontFamily: "'JetBrains Mono', monospace",
          fontWeight: 600, fontSize: 14, letterSpacing: "0.08em",
        }}>
          AUREM DEV
        </Link>
        <div style={{ display: "flex", gap: 10 }}>
          <Link to="/" data-testid="auth-back-home" className="btn-ghost"
                style={{ fontSize: 12 }}>
            <ArrowLeft size={13} /> Home
          </Link>
          {secondaryCta}
        </div>
      </nav>

      <main style={{
        position: "relative", zIndex: 1,
        padding: "clamp(40px, 8vh, 80px) clamp(20px, 5vw, 48px)",
      }}>
        {children}
      </main>
    </div>
  );
}
