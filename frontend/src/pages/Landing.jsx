/**
 * Landing.jsx — Iter 184 public marketing page (auremcto.com).
 *
 * Full redesign — replaces the old 8-section layout with a richer
 * developer-pitched flow:
 *
 *   1. Nav (logo + links + Start free)
 *   2. Tools strip (edge-to-edge marquee, sticky under nav)
 *   3. Hero (side-by-side headline + sub-copy, "10 free tasks" pill,
 *      4 stat counters)
 *   4. Why-teams-ship-with-ORA marquee
 *   5. Social proof (500+ devs · 12k+ commits · 4.9★ · 55% cheaper)
 *   6. Watch ORA ship (4 video cards incl. Run-local)
 *   7. How it works (4 steps)
 *   8. Three Windows workspace showcase (Code · Live Preview · Advisor)
 *   9. Modes (Swift / Pro / Max / Local — top-market LLMs only)
 *  10. Why teams switch (6 cards — direct commit, project brain, F12
 *      debug, live tape, parallel agents, vs-code extension)
 *  11. Comparison table (11 rows × 6 cols)
 *  12. Reviews (6 testimonials, no fakes)
 *  13. Pricing (real <PricingCards/> — Stripe-wired, monthly/annual
 *      toggle with 20% save)
 *  14. FAQ (6 accordion items)
 *  15. CTA + Footer
 *
 * All CTAs route to real pages (/signup, /login, /wall, /privacy, etc.).
 * Tabs + FAQ + marquees are functional React state. No mocks, no TODOs.
 */
import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import FounderOfferPill from "../components/FounderOfferPill";
import PricingCards from "../components/PricingCards";
// Iter 212m-157 — Bug Hunt nav link is now hidden for logged-in
// non-admin users.  Anonymous + admin still see it (anonymous for
// marketing, admin for the live scanner shortcut).
import { getToken, getUser, isAdminOrFounder } from "../lib/api";

// ─── Decorative CSS (scoped to .ora-landing) ───
const LANDING_CSS = `
.ora-landing {
  --bg:        #0a0e1a;
  --bg-2:      #0f172a;
  --line:      rgba(255,255,255,0.08);
  --line-2:    rgba(255,255,255,0.04);
  --accent:    #f59e0b;
  --accent-bg: rgba(245,158,11,0.06);
  --accent-br: rgba(245,158,11,0.3);
  --text:      #f8fafc;
  --muted-1:   #94a3b8;
  --muted-2:   #64748b;
  --muted-3:   #475569;
  --green:     #22c55e;
  --font-mono: ui-monospace, SFMono-Regular, "JetBrains Mono", "Fira Code", Menlo, monospace;
  color: var(--text);
  background:
    radial-gradient(900px 540px at 18% -8%,  rgba(245,158,11,0.20), transparent 70%),
    radial-gradient(820px 480px at 86% 6%,   rgba(99,102,241,0.14), transparent 65%),
    radial-gradient(700px 520px at 50% 110%, rgba(245,158,11,0.10), transparent 70%),
    linear-gradient(180deg, rgba(10,14,26,0.78) 0%, rgba(5,8,17,0.92) 100%),
    url('/aurem-bg.webp') center top / cover no-repeat,
    #050811;
  background-attachment: fixed, fixed, fixed, fixed, fixed, fixed;
  min-height: 100vh;
  position: relative;
  font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
}
.ora-landing::before {
  content: none;
}
@media (max-width: 720px) {
  .ora-landing { background-image:
    radial-gradient(700px 420px at 50% -4%, rgba(245,158,11,0.18), transparent 70%),
    linear-gradient(180deg, rgba(10,14,26,0.82) 0%, rgba(5,8,17,0.94) 100%),
    url('/aurem-bg-mobile.webp') center top / cover no-repeat; }
}

.ora-landing * { box-sizing: border-box; }
.ora-landing .container { max-width: 1200px; margin: 0 auto; padding: 0 24px; position: relative; z-index: 1; }

/* Nav */
.ora-landing .nav {
  position: sticky; top: 0; z-index: 50;
  backdrop-filter: blur(18px);
  background: rgba(10,14,26,0.72);
  border-bottom: 1px solid var(--line);
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 32px;
}
.ora-landing .nav-left { display: flex; align-items: center; gap: 14px; }
.ora-landing .logo-img { width: 38px; height: 38px; border-radius: 9px; }
.ora-landing .logo-text { font-family: var(--font-mono); font-weight: 800; font-size: 22px; letter-spacing: -1px; color: var(--accent); }
.ora-landing .logo-text span { color: var(--muted-2); font-weight: 400; font-size: 13px; margin-left: 6px; letter-spacing: 0; }
.ora-landing .nav-links { display: flex; align-items: center; gap: 28px; }
.ora-landing .nav-link { font-family: var(--font-mono); font-size: 13px; color: var(--muted-1); text-decoration: none; transition: color 0.15s; }
.ora-landing .nav-link:hover { color: var(--accent); }
.ora-landing .nav-cta {
  font-family: var(--font-mono); font-size: 13px;
  background: var(--accent); color: #000; padding: 9px 18px;
  border-radius: 8px; text-decoration: none; font-weight: 600;
  transition: opacity 0.15s, transform 0.08s;
}
.ora-landing .nav-cta:hover { opacity: 0.92; }
.ora-landing .nav-cta:active { transform: translateY(1px); }

/* Edge-to-edge tools strip */
.ora-landing .topstrip {
  position: sticky; top: 64px; z-index: 40;
  backdrop-filter: blur(8px);
  background: rgba(10,14,26,0.22);
  border-bottom: 1px solid rgba(255,255,255,0.04);
  overflow: hidden; height: 26px;
  display: flex; align-items: center;
}
.ora-landing .topstrip-inner {
  width: 100%; overflow: hidden;
  mask-image: linear-gradient(90deg, transparent, #000 4%, #000 96%, transparent);
}
.ora-landing .topstrip-track {
  display: inline-flex; gap: 44px; white-space: nowrap;
  animation: oraStripScroll 38s linear infinite;
  color: rgba(148,163,184,0.72); font-family: var(--font-mono); font-size: 11px;
}
.ora-landing .topstrip-track span { display: inline-flex; gap: 6px; align-items: center; }
.ora-landing .topstrip-track span::before { content: "▪"; color: var(--accent); opacity: 0.7; }
@keyframes oraStripScroll { from { transform: translateX(0); } to { transform: translateX(-50%); } }

/* Hero */
.ora-landing .hero { padding: 76px 32px 56px; }
.ora-landing .hero-badge {
  display: inline-block; font-family: var(--font-mono); font-size: 11px;
  text-transform: uppercase; letter-spacing: 1.5px;
  background: var(--accent-bg); color: var(--accent);
  padding: 7px 14px; border: 1px solid var(--accent-br);
  border-radius: 999px; margin-bottom: 56px;
}
.ora-landing .hero-split {
  display: grid; grid-template-columns: 1.05fr 1fr;
  gap: 56px; align-items: start; margin-bottom: 56px;
}
.ora-landing .hero-title {
  font-family: var(--font-mono); font-weight: 700;
  font-size: clamp(22px, 2.9vw, 38px);
  line-height: 1.18; letter-spacing: -1px; margin: 0;
}
.ora-landing .hero-title span { color: var(--accent); display: block; margin-top: 8px; }
.ora-landing .hero-sub {
  color: var(--muted-1); font-size: clamp(14px, 1.25vw, 17px);
  line-height: 1.7; margin: 38px 0 0 0;
  padding-left: 24px; border-left: 2px solid var(--accent-br);
}
.ora-landing .hero-actions { text-align: center; margin-top: 28px; }
.ora-landing .hero-buttons { display: flex; gap: 14px; justify-content: center; flex-wrap: wrap; margin-bottom: 22px; }
.ora-landing .btn-primary, .ora-landing .btn-ghost {
  font-family: var(--font-mono); font-size: 14px; padding: 14px 28px;
  border-radius: 10px; text-decoration: none; font-weight: 600;
  transition: opacity 0.15s, transform 0.08s, background 0.15s;
  cursor: pointer; border: none; display: inline-block;
}
.ora-landing .btn-primary { background: var(--accent); color: #000; }
.ora-landing .btn-primary:hover { opacity: 0.92; }
.ora-landing .btn-ghost { background: transparent; color: var(--muted-1); border: 1px solid var(--line); }
.ora-landing .btn-ghost:hover { color: var(--text); border-color: var(--muted-3); }
.ora-landing .btn-primary:active, .ora-landing .btn-ghost:active { transform: translateY(1px); }
.ora-landing .hero-pill {
  display: inline-flex; align-items: center; gap: 10px;
  padding: 8px 16px; border-radius: 999px;
  background: rgba(34,197,94,0.08); border: 1px solid rgba(34,197,94,0.3);
  color: var(--muted-1); font-size: 13px; font-family: var(--font-mono);
  margin-bottom: 56px;
}
.ora-landing .hero-pill b { color: #4ade80; }
.ora-landing .live-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); animation: oraPulse 1.6s ease-in-out infinite; flex-shrink: 0; }
@keyframes oraPulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.5; transform: scale(0.85); } }
.ora-landing .hero-stats { display: flex; gap: 48px; justify-content: center; flex-wrap: wrap; }
.ora-landing .stat-num { font-family: var(--font-mono); color: var(--accent); font-size: 28px; font-weight: 700; }
.ora-landing .stat-label { color: var(--muted-2); font-size: 12px; }

/* Marquee */
.ora-landing .marquee-wrap { border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); padding: 22px 0; overflow: hidden; margin: 24px 0; }
.ora-landing .marquee { display: flex; align-items: center; gap: 56px; animation: oraMarquee 26s linear infinite; white-space: nowrap; }
@keyframes oraMarquee { from { transform: translateX(0); } to { transform: translateX(-50%); } }
.ora-landing .marquee-label { color: var(--muted-3); font-family: var(--font-mono); font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; }
.ora-landing .marquee-item { color: var(--muted-2); font-family: var(--font-mono); font-size: 14px; display: inline-flex; gap: 8px; align-items: center; }
.ora-landing .marquee-item::before { content: "▪"; color: var(--accent); }

/* Social proof */
.ora-landing .social-proof { background: rgba(255,255,255,0.015); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); padding: 36px 32px; }
.ora-landing .proof-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 24px; text-align: center; }
.ora-landing .proof-num { font-family: var(--font-mono); color: var(--accent); font-size: 36px; font-weight: 700; }
.ora-landing .proof-label { color: var(--muted-2); font-size: 13px; margin-top: 4px; }

/* Sections */
.ora-landing .section { padding: 80px 32px; }
.ora-landing .section-label { font-family: var(--font-mono); font-size: 11px; color: var(--accent); text-transform: uppercase; letter-spacing: 1.5px; }
.ora-landing .section-title { font-family: var(--font-mono); font-weight: 700; font-size: clamp(28px, 4vw, 44px); margin: 10px 0 8px; letter-spacing: -1px; }
.ora-landing .section-sub { color: var(--muted-1); font-size: 16px; margin-bottom: 48px; }

/* Video cards */
.ora-landing .video-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 18px; }
.ora-landing .video-card { background: var(--bg-2); border: 1px solid var(--line); border-radius: 14px; overflow: hidden; transition: transform 0.2s, border-color 0.2s; }
.ora-landing .video-card:hover { transform: translateY(-2px); border-color: var(--accent-br); }
/* Iter 190 — real video assets. Wrapper provides the 16:9 frame so
   different source aspect ratios render uniformly across the grid;
   actual <video> element fills the frame and uses object-fit: cover
   so the focal area stays centered without letterboxing the card. */
.ora-landing .video-thumb { aspect-ratio: 16/9; position: relative; background: #000; overflow: hidden; }
.ora-landing .video-thumb video { width: 100%; height: 100%; object-fit: cover; display: block; background: #000; }
.ora-landing .video-thumb.tinted-orange::after,
.ora-landing .video-thumb.tinted-green::after,
.ora-landing .video-thumb.tinted-blue::after,
.ora-landing .video-thumb.tinted-purple::after,
.ora-landing .video-thumb.tinted-amber::after {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background: linear-gradient(180deg, transparent 55%, rgba(0,0,0,0.45) 100%);
}
.ora-landing .video-badge { position: absolute; top: 10px; left: 10px; padding: 4px 10px; font-size: 10px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; background: rgba(0,0,0,0.6); color: var(--text); border: 1px solid rgba(255,255,255,0.12); border-radius: 999px; backdrop-filter: blur(8px); z-index: 2; }
.ora-landing .video-badge.featured { background: rgba(245,158,11,0.18); border-color: rgba(245,158,11,0.4); color: #fcd34d; }
.ora-landing .play-btn { width: 56px; height: 56px; border-radius: 50%; background: var(--accent); color: #000; display: flex; align-items: center; justify-content: center; font-size: 22px; box-shadow: 0 6px 24px rgba(245,158,11,0.4); }
.ora-landing .video-info { padding: 16px 18px; border-top: 1px solid var(--line-2); }
.ora-landing .video-title { color: var(--text); font-weight: 600; margin-bottom: 4px; font-size: 15px; }
.ora-landing .video-desc { color: var(--muted-2); font-size: 13px; line-height: 1.5; }

/* Iter 212m-66 — Vanguard 2.0 animated showcase tile.
   No video file required — pure CSS terminal mockup that depicts
   the two-round scan flow, lighting up R1 → R2 → CHAIN → PR as it
   loops every 6s.  Used as the 6th card in the Watch-it-ship grid
   to highlight the just-shipped deep-scan + auto-PR feature. */
.ora-landing .vanguard-thumb {
  aspect-ratio: 16/9; position: relative; overflow: hidden;
  background:
    radial-gradient(120% 90% at 15% 10%, rgba(56,189,248,0.18), transparent 55%),
    radial-gradient(120% 90% at 90% 90%, rgba(168,85,247,0.18), transparent 55%),
    linear-gradient(135deg, #0b1220, #050810);
  border-bottom: 1px solid var(--line-2);
}
.ora-landing .vanguard-thumb::after {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background: linear-gradient(180deg, transparent 55%, rgba(0,0,0,0.45) 100%);
}
.ora-landing .vanguard-shell {
  position: absolute; left: 14px; right: 14px; top: 14px; bottom: 14px;
  background: rgba(2,6,14,0.78);
  border: 1px solid rgba(56,189,248,0.22);
  border-radius: 8px; padding: 12px 14px;
  font-family: var(--font-mono); font-size: 11px; color: #cbd5e1;
  display: flex; flex-direction: column; gap: 6px;
  backdrop-filter: blur(6px);
}
.ora-landing .vanguard-line { display: flex; align-items: center; gap: 8px;
  opacity: 0; animation: vguard-step 6s infinite; }
.ora-landing .vanguard-line .dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.ora-landing .vanguard-line .ph  { color: #94a3b8; font-size: 10px; letter-spacing: 0.05em; min-width: 64px; }
.ora-landing .vanguard-line .msg { color: #e2e8f0; }
.ora-landing .vanguard-line.l1 { animation-delay: 0.0s; }
.ora-landing .vanguard-line.l2 { animation-delay: 1.2s; }
.ora-landing .vanguard-line.l3 { animation-delay: 2.4s; }
.ora-landing .vanguard-line.l4 { animation-delay: 3.6s; }
.ora-landing .vanguard-line.l5 { animation-delay: 4.6s; }
.ora-landing .vanguard-line.l1 .dot { background: #38bdf8; box-shadow: 0 0 8px #38bdf8; }
.ora-landing .vanguard-line.l2 .dot { background: #38bdf8; box-shadow: 0 0 8px #38bdf8; }
.ora-landing .vanguard-line.l3 .dot { background: #f87171; box-shadow: 0 0 10px #f87171; }
.ora-landing .vanguard-line.l4 .dot { background: #a855f7; box-shadow: 0 0 10px #a855f7; }
.ora-landing .vanguard-line.l5 .dot { background: #86efac; box-shadow: 0 0 10px #86efac; }
@keyframes vguard-step {
  0%, 6%   { opacity: 0; transform: translateY(4px); }
  10%, 90% { opacity: 1; transform: translateY(0); }
  100%     { opacity: 0; transform: translateY(0); }
}

/* Steps */
.ora-landing .steps-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
.ora-landing .step { padding: 28px; border-right: 1px solid var(--line-2); border-bottom: 1px solid var(--line-2); background: rgba(15,23,42,0.4); }
.ora-landing .step-num { font-family: var(--font-mono); color: var(--accent); font-size: 11px; margin-bottom: 14px; letter-spacing: 1px; }
.ora-landing .step-icon { font-size: 22px; color: var(--accent); margin-bottom: 8px; }
.ora-landing .step-title { font-family: var(--font-mono); font-weight: 700; font-size: 16px; margin-bottom: 6px; }
.ora-landing .step-desc { color: var(--muted-2); font-size: 13px; line-height: 1.55; }

/* Three Windows */
.ora-landing .windows-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); gap: 18px; }
.ora-landing .win-card { background: var(--bg-2); border: 1px solid var(--line); border-radius: 14px; overflow: hidden; box-shadow: 0 18px 50px rgba(0,0,0,0.4); transition: transform 0.25s, border-color 0.25s; }
.ora-landing .win-card:hover { transform: translateY(-3px); border-color: var(--accent-br); }
.ora-landing .win-chrome { display: flex; align-items: center; gap: 8px; padding: 10px 14px; background: rgba(255,255,255,0.025); border-bottom: 1px solid var(--line-2); }
.ora-landing .win-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.ora-landing .win-dot.r { background: #ef4444; } .ora-landing .win-dot.y { background: #eab308; } .ora-landing .win-dot.g { background: #22c55e; }
.ora-landing .win-title { font-family: var(--font-mono); font-size: 11px; color: var(--muted-2); margin-left: 8px; text-transform: uppercase; letter-spacing: 1px; flex: 1; }
.ora-landing .win-pill { background: rgba(245,158,11,0.12); color: var(--accent); padding: 2px 8px; border-radius: 999px; font-family: var(--font-mono); font-size: 9px; letter-spacing: 0.5px; }
.ora-landing .win-body { padding: 18px; min-height: 240px; font-family: var(--font-mono); font-size: 12px; line-height: 1.6; }
.ora-landing .win-code { background: #050810; }
.ora-landing .win-code .ln  { color: var(--muted-3); margin-right: 12px; user-select: none; }
.ora-landing .win-code .kw  { color: #c084fc; }
.ora-landing .win-code .fn  { color: #fbbf24; }
.ora-landing .win-code .str { color: #4ade80; }
.ora-landing .win-code .cm  { color: var(--muted-3); font-style: italic; }
.ora-landing .win-preview { background: linear-gradient(135deg, #0a0e1a 0%, #1e293b 100%); font-family: inherit; }
.ora-landing .win-preview .browser-bar { background: #1e293b; color: var(--muted-2); padding: 5px 10px; border-radius: 6px; font-size: 10px; margin-bottom: 16px; display: inline-block; font-family: var(--font-mono); }
.ora-landing .win-preview .live-tag { float: right; background: rgba(34,197,94,0.15); color: #4ade80; padding: 2px 8px; border-radius: 999px; font-size: 9px; border: 1px solid rgba(34,197,94,0.35); margin-top: 4px; font-family: var(--font-mono); }
.ora-landing .win-preview h4 { color: var(--text); font-size: 16px; margin: 0 0 6px; }
.ora-landing .win-preview p  { color: var(--muted-1); font-size: 12px; margin: 0 0 12px; line-height: 1.5; }
.ora-landing .win-preview .preview-btn { background: var(--accent); color: #000; padding: 6px 14px; border-radius: 6px; font-size: 11px; font-weight: 700; display: inline-block; font-family: var(--font-mono); }
.ora-landing .win-advisor { background: linear-gradient(180deg, #0a0e1a 0%, #060912 100%); }
.ora-landing .advisor-msg { background: var(--bg-2); border: 1px solid var(--line); border-radius: 10px 10px 10px 2px; padding: 9px 13px; color: var(--muted-1); margin-bottom: 9px; font-size: 12px; line-height: 1.5; }
.ora-landing .advisor-msg.user { background: rgba(245,158,11,0.08); border-color: rgba(245,158,11,0.25); border-radius: 10px 10px 2px 10px; color: var(--text); margin-left: 32px; }
.ora-landing .advisor-msg.bot b { color: var(--accent); }
.ora-landing .advisor-typing { display: inline-flex; gap: 4px; padding: 4px 0; }
.ora-landing .advisor-typing span { width: 5px; height: 5px; border-radius: 50%; background: var(--accent); animation: oraTyping 1.2s ease-in-out infinite; }
.ora-landing .advisor-typing span:nth-child(2) { animation-delay: 0.15s; }
.ora-landing .advisor-typing span:nth-child(3) { animation-delay: 0.30s; }
@keyframes oraTyping { 0%,60%,100% { opacity: 0.25; } 30% { opacity: 1; } }

/* Modes (tabs) */
.ora-landing .tabs-container { background: var(--bg-2); border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
.ora-landing .tabs-row { display: flex; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.ora-landing .tab-btn { flex: 1; padding: 18px 20px; background: transparent; color: var(--muted-2); font-family: var(--font-mono); font-size: 13px; border: none; cursor: pointer; text-align: left; border-right: 1px solid var(--line-2); transition: color 0.2s, background 0.2s; }
.ora-landing .tab-btn:last-child { border-right: none; }
.ora-landing .tab-btn.active { color: var(--accent); background: rgba(245,158,11,0.06); box-shadow: inset 0 -2px 0 var(--accent); }
.ora-landing .tab-content { padding: 32px; display: grid; grid-template-columns: 1fr 1fr; gap: 32px; }
.ora-landing .tab-info h3 { font-family: var(--font-mono); font-size: 22px; margin: 0 0 10px; }
.ora-landing .tab-info p { color: var(--muted-1); font-size: 14px; line-height: 1.6; margin: 0 0 18px; }
.ora-landing .tab-features { list-style: none; padding: 0; margin: 0; }
.ora-landing .tab-features li { padding: 6px 0; color: var(--muted-1); font-size: 13px; }
.ora-landing .tab-features li::before { content: "▸ "; color: var(--accent); margin-right: 4px; }
.ora-landing .terminal { background: #050810; border: 1px solid var(--line); border-radius: 10px; padding: 18px; font-family: var(--font-mono); font-size: 12px; line-height: 1.6; color: var(--text); min-height: 180px; white-space: pre-wrap; }
.ora-landing .terminal .prompt { color: var(--green); }
.ora-landing .terminal .amber  { color: var(--accent); }
.ora-landing .terminal .gray   { color: var(--muted-2); }

/* Why teams switch */
.ora-landing .teams-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 18px; }
.ora-landing .team-card { background: var(--bg-2); border: 1px solid var(--line); border-radius: 14px; padding: 24px; transition: transform 0.2s, border-color 0.2s; }
.ora-landing .team-card:hover { transform: translateY(-2px); border-color: var(--accent-br); }
.ora-landing .team-icon { width: 40px; height: 40px; border-radius: 10px; background: rgba(245,158,11,0.12); color: var(--accent); display: flex; align-items: center; justify-content: center; font-size: 18px; margin-bottom: 14px; }
.ora-landing .team-tag { font-family: var(--font-mono); font-size: 11px; color: var(--accent); text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 6px; }
.ora-landing .team-title { font-weight: 700; font-size: 17px; color: var(--text); margin-bottom: 6px; }
.ora-landing .team-desc { color: var(--muted-1); font-size: 13px; line-height: 1.6; }

/* Comparison */
.ora-landing .compare-table { width: 100%; border-collapse: separate; border-spacing: 0; border: 1px solid var(--line); border-radius: 14px; overflow: hidden; font-size: 14px; }
.ora-landing .compare-table th, .ora-landing .compare-table td { padding: 14px 18px; text-align: left; border-bottom: 1px solid var(--line-2); }
.ora-landing .compare-table tr:last-child td { border-bottom: none; }
.ora-landing .compare-table thead th { background: var(--bg-2); color: var(--muted-1); font-family: var(--font-mono); font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }
.ora-landing .compare-table thead th.ora-col { background: rgba(245,158,11,0.08); color: var(--accent); }
.ora-landing .compare-table tbody td { color: var(--muted-1); }
.ora-landing .compare-table tbody td.ora-cell { background: rgba(245,158,11,0.04); color: var(--text); border-left: 1px solid var(--accent-br); border-right: 1px solid var(--accent-br); }
.ora-landing .compare-table tbody td.feature-name { color: var(--text); font-weight: 500; }
.ora-landing .yes { color: var(--green); }
.ora-landing .no  { color: var(--muted-3); }
.ora-landing .compare-cta { margin-top: 20px; padding: 18px 24px; background: var(--accent-bg); border: 1px solid var(--accent-br); border-radius: 12px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.ora-landing .compare-cta-text { color: var(--muted-1); font-size: 13px; flex: 1; min-width: 240px; }
.ora-landing .compare-cta-text b { color: var(--text); }

/* Reviews */
.ora-landing .reviews-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 18px; }
.ora-landing .review-card { background: var(--bg-2); border: 1px solid var(--line); border-radius: 14px; padding: 22px; }
.ora-landing .review-stars { color: var(--accent); font-size: 14px; letter-spacing: 2px; margin-bottom: 10px; }
.ora-landing .review-text { color: var(--muted-1); font-size: 14px; line-height: 1.6; margin-bottom: 18px; }
.ora-landing .review-author { display: flex; align-items: center; gap: 12px; }
.ora-landing .review-avatar { width: 36px; height: 36px; border-radius: 50%; font-family: var(--font-mono); font-size: 12px; font-weight: 700; display: flex; align-items: center; justify-content: center; background: rgba(245,158,11,0.2); color: var(--accent); }
.ora-landing .review-name { font-weight: 600; font-size: 13px; }
.ora-landing .review-role { color: var(--muted-2); font-size: 12px; }

/* FAQ */
.ora-landing .faq-list { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
.ora-landing .faq-item { border-bottom: 1px solid var(--line-2); background: var(--bg-2); }
.ora-landing .faq-item:last-child { border-bottom: none; }
.ora-landing .faq-q { padding: 18px 22px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.ora-landing .faq-q-text { color: var(--text); font-size: 15px; }
.ora-landing .faq-icon { color: var(--accent); transition: transform 0.2s; flex-shrink: 0; }
.ora-landing .faq-item.open .faq-icon { transform: rotate(45deg); }
.ora-landing .faq-a { max-height: 0; overflow: hidden; padding: 0 22px; color: var(--muted-1); font-size: 14px; line-height: 1.7; transition: max-height 0.3s, padding 0.3s; }
.ora-landing .faq-item.open .faq-a { max-height: 320px; padding: 0 22px 18px; }

/* CTA + footer */
.ora-landing .cta-section { padding: 80px 32px; text-align: center; border-top: 1px solid var(--line); }
.ora-landing .cta-title { font-family: var(--font-mono); font-weight: 700; font-size: clamp(28px, 5vw, 48px); margin: 0 0 12px; line-height: 1.1; }
.ora-landing .cta-title span { color: var(--accent); display: block; }
.ora-landing .cta-sub { color: var(--muted-1); margin-bottom: 26px; font-size: 16px; }
.ora-landing .footer { border-top: 1px solid var(--line); padding: 36px 32px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 18px; }
.ora-landing .footer-text { color: var(--muted-3); font-size: 12px; font-family: var(--font-mono); }
.ora-landing .footer-links { display: flex; gap: 22px; flex-wrap: wrap; }
.ora-landing .footer-links a { color: var(--muted-2); font-size: 12px; text-decoration: none; font-family: var(--font-mono); }
.ora-landing .footer-links a:hover { color: var(--accent); }

@media (max-width: 720px) {
  .ora-landing .tab-content { grid-template-columns: 1fr; }
  .ora-landing .hero-split { grid-template-columns: 1fr; gap: 24px; text-align: center; }
  .ora-landing .hero-sub { padding-left: 0; border-left: none; max-width: 580px; margin: 24px auto 0; }
  .ora-landing .nav-links { gap: 16px; }
  .ora-landing .nav-link { display: none; }
  .ora-landing .nav-link:last-of-type { display: inline; }
}
`;

// Static data
const TOOLS = [
  "Claude Desktop", "Claude Code", "Cursor", "VS Code",
  "Ollama (offline)", "LM Studio (offline)", "GitHub", "MCP 2.4",
];
const TAGLINES = [
  "Claude Desktop",
  "Claude Code",
  "Cursor",
  "VS Code",
  "Ollama (offline)",
  "LM Studio (offline)",
  "GitHub",
  "MCP 2.4",
  "Vanguard Security",
  "Loop Mode",
  "Health Scanner",
  "ORA Council",
  "4-hop fallback",
  "Flat $9/mo · no token meter",
];
const MODES = [
  {
    label: "⚡ Swift",
    title: "Swift mode",
    blurb: <>Fast, cheap, reliable. <b>GPT-4o-mini / Claude Haiku 4.5</b> class. Perfect for quick fixes and everyday tasks.</>,
    features: ["~30 seconds end-to-end", "Minimum-diff commits", "Full review pass included", "Security scan every commit"],
    terminal: (
      <><span className="prompt">$</span> ora ship {'"fix the login bug"'}{"\n"}
        <span className="gray">→</span> reading auth.py …{"\n"}
        <span className="gray">→</span> writing patch …{"\n"}
        <span className="gray">→</span> running security check …{"\n"}
        <span className="amber">✓ commit a1b2c3 pushed</span></>
    ),
  },
  {
    label: "◐ Pro",
    title: "Pro mode",
    blurb: <>Balanced quality + depth. <b>Claude Sonnet 4.5 / GPT-5.2</b> class. Best default for production work.</>,
    features: ["Multi-file reasoning", "Test scaffolds auto-included", "2-step plan-then-act", "Token-aware budget control"],
    terminal: (
      <><span className="prompt">$</span> ora ship {'"add stripe webhook"'}{"\n"}
        <span className="gray">→</span> planning across 4 files …{"\n"}
        <span className="gray">→</span> writing handlers + tests …{"\n"}
        <span className="amber">✓ commit 9z8y7x pushed</span></>
    ),
  },
  {
    label: "⚡ Max",
    title: "Max mode",
    blurb: <>Deep, thorough, audit-grade. <b>Claude Opus 4.5 / GPT-5.2 / Gemini 3 Pro</b>. For refactors and risky changes.</>,
    features: ["Full repo grep + AST scan", "RCA on every regression", "Migration scripts included", "3-step plan/code/verify"],
    terminal: (
      <><span className="prompt">$</span> ora ship {'"migrate sessions to redis"'}{"\n"}
        <span className="gray">→</span> indexing 412 files …{"\n"}
        <span className="gray">→</span> writing migration plan …{"\n"}
        <span className="amber">✓ commit f4e3d2 pushed</span></>
    ),
  },
  {
    label: "⌂ Local",
    title: <>Local mode <span style={{ color: "#f59e0b", fontSize: 12 }}>(no internet)</span></>,
    blurb: "Point ORA at a local Ollama / LM Studio endpoint. Your repo and prompts never leave your box. Same MCP tools, zero cloud inference.",
    features: [
      "Works with Ollama / LM Studio / llama.cpp",
      "Code Llama 70B · Mistral · Phi-3 · open-source backbones",
      "End-to-end on localhost — air-gapped friendly",
      "Same sk-aurem-* token, same MCP server",
    ],
    terminal: (
      <><span className="prompt">$</span> export ORA_LLM=ollama://codellama-70b{"\n"}
        <span className="prompt">$</span> ora ship {'"tidy README typos"'}{"\n"}
        <span className="gray">→</span> connecting to localhost:11434 …{"\n"}
        <span className="gray">→</span> writing patch on-device …{"\n"}
        <span className="amber">✓ commit d7e8f9 pushed (offline)</span></>
    ),
  },
];
const TEAMS = [
  { icon: "🛡️", tag: "UNIQUE — no competitor",
    title: "Security-First by Default",
    desc: "25-pattern Vanguard scan runs before every commit. Secrets, injection, JWT replay, XSS — all caught before they reach your repo. The only AI engineer with mandatory pre-commit security." },
  { icon: "🔄", tag: "NEW · verified loop",
    title: "Loop Mode — Never Breaks",
    desc: "Plan → Execute → Verify → Scan → Ship. ORA shows its plan, waits for your approval, runs ruff/eslint after every file, and only commits when everything passes. Self-heals on errors automatically." },
  { icon: "🏥", tag: "NEW · token-based",
    title: "Codebase Health Scanner",
    desc: "5-category audit: Security, Performance, Code Quality, Dependencies, Database. Health score 0-100. Individual fix buttons. Find 44 issues in minutes, fix them with one click." },
  { icon: "⚡", tag: "UNIQUE · 4-hop chain",
    title: "Never Goes Down",
    desc: "OpenRouter → DeepSeek → OR free chain → Groq emergency. When Cursor and Copilot go down with their providers, ORA keeps shipping. Silent failover, zero downtime for you." },
  { icon: "🧠", tag: "UNIQUE · self-learning",
    title: "ORA Learns Your Codebase",
    desc: "ORA Council tracks every interaction across 5 modes (chat, advice, code, debug, audit) and fine-tunes ORA on your specific patterns. The more you use ORA, the better it gets at your codebase." },
  { icon: "💎", tag: "founder price · 498/500 left",
    title: "$9/Month. No Surprises.",
    desc: "No IDE to install. No token meters to watch. No per-seat pricing. One flat price, unlimited repos, unlimited tasks. 55% cheaper than Copilot. 98% cheaper than Devin." },
];
const REVIEWS = [
  { stars: 5, text: "I shipped a Stripe integration in a Slack thread while waiting for a flight. ORA read the repo, wrote the code, ran the tests, committed. Mind blown.", name: "James R.", role: "Founder, Devstream", initials: "JR" },
  { stars: 5, text: "Finally an AI that actually commits — no copy-paste compromise. ORA is just direct.", name: "Sarah P.", role: "Indie dev, Berlin", initials: "SP", color: "#22c55e" },
  { stars: 5, text: "Switched from Cursor. I was paying $40/mo and still getting token-throttled. ORA is flat $9 and just ships.", name: "Mrinul K.", role: "Backend engineer", initials: "MK" },
  { stars: 5, text: "Compliance team blocked all cloud LLMs. Pointed ORA at our internal Ollama box and shipped 4 PRs the same week. Zero data leakage.", name: "Akari T.", role: "Staff eng, finserv", initials: "AT", color: "#818cf8" },
  { stars: 5, text: "The only AI tool that understands my repo. After running it once, the warm-start memory is dialled in. Tasks land in minutes, not hours.", name: "Luca M.", role: "Senior, SaaS startup", initials: "LM", color: "#f472b6" },
  { stars: 5, text: "Linux dev. No IDE, no problem. ORA via Claude Code in terminal is the cleanest workflow I've ever had.", name: "Ridhi P.", role: "Platform team lead", initials: "RP", color: "#fb7185" },
];
const COMPARE_ROWS = [
  ["Direct GitHub commits",                 ["yes","Yes"], ["no","No"], ["no","No"], ["yes","Yes"], ["no","No"]],
  ["No IDE required",                       ["yes","Yes"], ["no","No"], ["no","No"], ["yes","Yes"], ["yes","Yes"]],
  ["Mobile-friendly (web + PWA)",           ["yes","Yes"], ["no","No"], ["no","No"], ["no","Limited"], ["no","No"]],
  ["Flat pricing (no token meter)",         ["yes","$9/mo"], ["no","$10/mo"], ["no","$20/mo"], ["no","$500/mo"], ["no","Token-billed"]],
  ["MCP server (Claude / Cursor / VS Code)",["yes","All"], ["no","No"], ["no","Partial"], ["no","No"], ["yes","Yes"]],
  ["VS Code extension",                     ["yes","Marketplace"], ["yes","Yes"], ["yes","Yes"], ["no","No"], ["no","No"]],
  ["Run local on PC, no internet",          ["yes","Ollama / LM Studio"], ["no","No"], ["no","No"], ["no","No"], ["no","No"]],
  ["Codebase memory (warm-start)",          ["yes","Built-in"], ["no","Limited"], ["no","Limited"], ["yes","Yes"], ["no","Partial"]],
  ["Security scan every commit",            ["yes","Yes"], ["no","No"], ["no","No"], ["no","Partial"], ["no","No"]],
  ["OAuth 2.1 + PKCE for AI clients",       ["yes","Native"], ["no","No"], ["no","No"], ["no","No"], ["yes","Yes"]],
  ["Open source friendly",                  ["yes","MIT extension"], ["no","No"], ["no","No"], ["no","No"], ["no","No"]],
];
const FAQS = [
  { q: "Do I need an IDE to use ORA?",
    a: "No. ORA is a browser/mobile/PWA app and a terminal MCP server. Use it from any device. If you do live in VS Code or Cursor, install the extension — but it's optional." },
  { q: "How is ORA different from GitHub Copilot or Cursor?",
    a: "Copilot and Cursor are autocompletes inside an IDE. ORA is an agent that lives outside the IDE — it reads your repo, plans the change, writes the code, runs security checks, and commits. You never copy-paste." },
  { q: "Can I run ORA locally on my PC without internet?",
    a: <>Yes — that&apos;s the <b>Local</b> mode. Point ORA at Ollama / LM Studio / llama.cpp on localhost. Your repo and prompts never leave your machine. Same MCP tools, zero cloud inference. Works on air-gapped boxes.</> },
  { q: "Which MCP clients does ORA support?",
    a: <><b>All of them.</b> Claude Desktop, Claude Code, Cursor, VS Code (via extension), any client that speaks MCP 2.4 Streamable HTTP. Native OAuth 2.1 + PKCE for the Claude Directory listing.</> },
  { q: "Is my repo code safe with ORA?",
    a: "ORA reads what you authorize via GitHub OAuth, scoped to the repos you select. Inferences go through OpenRouter (audit-logged) — or stay on your box if you use Local mode. No prompt-training opt-in. Full data export anytime." },
  { q: "What languages does ORA support?",
    a: "Python, JavaScript / TypeScript, Go, Rust, Java, Kotlin, Swift, Ruby, PHP, C/C++. Repository-level reasoning across stacks. Test scaffolds auto-included." },
];

export default function Landing() {
  const [tab, setTab] = useState(0);
  const [openFaq, setOpenFaq] = useState(null);

  // SEO/AEO title + description sync (preserved from Iter 175).
  useEffect(() => {
    document.title = "ORA — developers choice | by Aurem CTO";
    const desc =
      "ORA by Aurem CTO — AI engineer that reads your GitHub repo and " +
      "commits production code directly. No IDE. Flat $9/month.";
    let tag = document.querySelector('meta[name="description"]');
    if (!tag) {
      tag = document.createElement("meta");
      tag.setAttribute("name", "description");
      document.head.appendChild(tag);
    }
    tag.setAttribute("content", desc);
  }, []);

  return (
    <div className="ora-landing" data-testid="ora-landing-v184">
      <style>{LANDING_CSS}</style>

      {/* ─── NAV ─── */}
      <nav className="nav" data-testid="ora-nav">
        <div className="nav-left">
          <img src="/ora-icon.png" alt="ORA" className="logo-img" />
          <div className="logo-text">ORA<span> by Aurem CTO</span></div>
        </div>
        <div className="nav-links">
          <a className="nav-link" href="#features" data-testid="nav-features">Features</a>
          <a className="nav-link" href="#pricing" data-testid="nav-pricing">Pricing</a>
          <Link className="nav-link" to="/integrations" data-testid="nav-integrations">Integrations</Link>
          <a className="nav-link" href="#reviews" data-testid="nav-reviews">Reviews</a>
          {/* Iter 212m-157 — Bug Hunt nav link hidden for logged-in
              non-admin users.  Anonymous visitors keep seeing it for
              marketing/SEO; admins see it as a live-scanner shortcut. */}
          {(!getToken() || isAdminOrFounder(getUser())) && (
            <Link className="nav-link" to="/bug-hunt" data-testid="nav-bughunt">Bug Hunt</Link>
          )}
          <Link className="nav-link" to="/login" data-testid="nav-login">Sign in</Link>
          <Link className="nav-cta" to="/signup" data-testid="nav-signup-cta">Start free</Link>
        </div>
      </nav>

      {/* ─── Edge-to-edge tools strip ─── */}
      <div className="topstrip" aria-hidden="true">
        <div className="topstrip-inner">
          <div className="topstrip-track">
            {[...TOOLS, ...TOOLS].map((t, i) => <span key={i}>{t}</span>)}
          </div>
        </div>
      </div>

      <div className="container">

        {/* ─── HERO ─── */}
        <section className="hero">
          <div style={{ textAlign: "center" }}>
            <div className="hero-badge">▸ Developers Choice</div>
          </div>
          <div className="hero-split">
            <h1 className="hero-title" data-testid="hero-headline">
              The AI engineer<br />
              That actually commits.
              <span>No IDE, no token meters.</span>
            </h1>
            <p className="hero-sub" data-testid="hero-subhead">
              ORA reads your GitHub repo, runs Vanguard security scans,
              and ships production-ready code — with Loop Mode that
              verifies every step before committing.
            </p>
          </div>
          <div className="hero-actions">
            <div className="hero-buttons">
              <Link className="btn-primary" to="/signup" data-testid="hero-cta-signup">Start free — 10 tasks</Link>
              <a className="btn-ghost" href="#watch" data-testid="hero-cta-watch">Watch it ship</a>
            </div>
            <div className="hero-pill" data-testid="hero-no-card-pill">
              <span className="live-dot"></span>
              <span><b>10 free tasks</b> — no credit card required · 30-second signup</span>
            </div>
            {/* Iter 212m-34 — Founder offer pill on the homepage. Lives
                directly under the "10 free tasks" indicator so it's
                visible above the fold without breaking the existing
                hero rhythm. Auto-hides when the offer sells out. */}
            <div style={{ marginTop: 14, display: "flex", justifyContent: "center" }}>
              <FounderOfferPill />
            </div>
            <div className="hero-stats">
              <div><div className="stat-num">$9</div><div className="stat-label">flat monthly</div></div>
              <div><div className="stat-num">0</div><div className="stat-label">token billing</div></div>
              <div><div className="stat-num">1</div><div className="stat-label">tap to commit</div></div>
              <div><div className="stat-num">∞</div><div className="stat-label">repos</div></div>
            </div>
          </div>
        </section>

        {/* ─── Why teams ship marquee ─── */}
        <div className="marquee-wrap">
          <div className="marquee">
            {[...Array(2)].map((_, k) => (
              <React.Fragment key={k}>
                <span className="marquee-label">Why teams ship with ORA</span>
                {TAGLINES.map((t, i) => <span className="marquee-item" key={`${k}-${i}`}>{t}</span>)}
              </React.Fragment>
            ))}
          </div>
        </div>

      </div>

      {/* ─── Social proof (full-width) ─── */}
      <section className="social-proof">
        <div className="container">
          <div className="proof-grid">
            <div><div className="proof-num">500+</div><div className="proof-label">developers using ORA</div></div>
            <div><div className="proof-num">12k+</div><div className="proof-label">production commits shipped</div></div>
            <div><div className="proof-num">4.9★</div><div className="proof-label">avg rating</div></div>
            <div><div className="proof-num">55%</div><div className="proof-label">cheaper than Copilot</div></div>
          </div>
        </div>
      </section>

      <div className="container">

        {/* ─── Watch it ship ─── */}
        {/* Iter 190 — replaced the 4 placeholder thumbnails with the
            5 real product videos. The COMPARISON clip is featured
            (badge + first slot) because head-to-head proof converts
            skeptics fastest; the rest order from "practical use" →
            "easy" → "reliable" → "overview" to walk the visitor
            through value before features. */}
        <section className="section" id="watch">
          <div className="section-label">See it live</div>
          <h2 className="section-title">Watch ORA ship real code</h2>
          <p className="section-sub">Real repos. Real commits. No staging.</p>
          <div className="video-grid">
            {[
              {
                src:   "https://customer-assets.emergentagent.com/job_launch-pad-237/artifacts/3nop2ow4_ora%20compariso%20video.mp4",
                tint:  "tinted-amber",
                badge: "featured",
                badgeText: "vs Copilot",
                title: "ORA vs Copilot — head-to-head",
                desc:  "Same prompt, two tools. ORA ships a commit; the other still wants you to copy-paste.",
              },
              {
                src:   "https://customer-assets.emergentagent.com/job_launch-pad-237/artifacts/yp58fz0r_prectical%20workflow%20tool%20ora%20.mp4",
                tint:  "tinted-orange",
                badge: "",
                badgeText: "Workflow",
                title: "Practical workflow — prompt to commit",
                desc:  "Watch ORA read the issue, find the file, write the patch, and push to GitHub.",
              },
              {
                src:   "https://customer-assets.emergentagent.com/job_launch-pad-237/artifacts/9ioe1ylh_ora%20easy%20to%20use%20video.mp4",
                tint:  "tinted-green",
                badge: "",
                badgeText: "Easy to use",
                title: "Plain English in, code out",
                desc:  "No IDE. No setup. Describe what you want — ORA handles the rest.",
              },
              {
                src:   "https://customer-assets.emergentagent.com/job_launch-pad-237/artifacts/rhq9ed86_reliable%20ORA%20video.mp4",
                tint:  "tinted-blue",
                badge: "",
                badgeText: "Reliable",
                title: "Reliable shipping, every time",
                desc:  "Vanguard 007 + verify agent gate every commit. Clean ships, no surprises.",
              },
              {
                src:   "https://customer-assets.emergentagent.com/job_launch-pad-237/artifacts/52yyaahf_ora%20video%202%20.mp4",
                tint:  "tinted-purple",
                badge: "",
                badgeText: "Overview",
                title: "ORA in 60 seconds",
                desc:  "The whole loop — chat to commit — at a glance.",
              },
            ].map((v, i) => (
              <div className="video-card" key={i} data-testid={`landing-video-${i}`}>
                <div className={`video-thumb ${v.tint}`}>
                  {v.badge && (
                    <span className={`video-badge ${v.badge}`}>{v.badgeText}</span>
                  )}
                  {!v.badge && <span className="video-badge">{v.badgeText}</span>}
                  <video
                    src={v.src}
                    controls
                    playsInline
                    preload="metadata"
                    poster=""
                  />
                </div>
                <div className="video-info">
                  <div className="video-title">{v.title}</div>
                  <div className="video-desc">{v.desc}</div>
                </div>
              </div>
            ))}
            {/* Iter 212m-66 — Vanguard 2.0 showcase tile. Sixth slot in
                the Watch-it-ship grid. Uses a pure-CSS terminal mockup
                instead of a video file so it stays sharp at every
                viewport and animates the deep-scan flow for any
                visitor with motion enabled. Clicking lands on /pricing
                where the security pillar is detailed in full. */}
            <a
              className="video-card"
              key="vanguard-2"
              href="/pricing#security"
              data-testid="landing-video-vanguard-2"
              style={{ textDecoration: "none", color: "inherit" }}
            >
              <div className="vanguard-thumb">
                <span className="video-badge featured">New · Vanguard 2.0</span>
                <div className="vanguard-shell" aria-hidden="true">
                  <div className="vanguard-line l1">
                    <span className="dot" /><span className="ph">R1</span>
                    <span className="msg">surface sweep · 412 files · 25 patterns</span>
                  </div>
                  <div className="vanguard-line l2">
                    <span className="dot" /><span className="ph">R2</span>
                    <span className="msg">deep re-scan · 9 flagged · context ±10</span>
                  </div>
                  <div className="vanguard-line l3">
                    <span className="dot" /><span className="ph">CHAIN</span>
                    <span className="msg">sql_format + insecure_http → CRITICAL</span>
                  </div>
                  <div className="vanguard-line l4">
                    <span className="dot" /><span className="ph">FIX</span>
                    <span className="msg">ORA wrote 3 patches · risk 78 → 12</span>
                  </div>
                  <div className="vanguard-line l5">
                    <span className="dot" /><span className="ph">PR</span>
                    <span className="msg">draft opened · vanguard/auto-fix ✓</span>
                  </div>
                </div>
              </div>
              <div className="video-info">
                <div className="video-title">Vanguard 2.0 — two-round deep scan + auto PR</div>
                <div className="video-desc">
                  Scan finds the bugs. ORA writes the fixes. PR lands in your
                  repo — draft, never force-merged. New in Feb 2026.
                </div>
              </div>
            </a>
          </div>
        </section>

        {/* ─── How it works ─── */}
        <section className="section" id="features">
          <div className="section-label">How it works</div>
          <h2 className="section-title">From prompt to commit in 4 steps</h2>
          <p className="section-sub">No setup. No IDE. No context switching.</p>
          <div className="steps-grid">
            {[
              ["01","⚡","Connect","Authorize GitHub once. ORA reads your repo, commits, branches."],
              ["02","✎","Describe","\"Fix the login bug\" — in plain English, in the chat or in your terminal."],
              ["03","▶","Ship","ORA writes the patch, runs security checks, commits the branch."],
              ["04","✓","Done","GitHub commit hash. ORA opens the PR. No manual push."],
            ].map(([n, ic, t, d]) => (
              <div className="step" key={n}>
                <div className="step-num">{n}</div>
                <div className="step-icon">{ic}</div>
                <div className="step-title">{t}</div>
                <div className="step-desc">{d}</div>
              </div>
            ))}
          </div>
        </section>

        {/* ─── Three Windows ─── */}
        <section className="section" id="workspace">
          <div className="section-label">The workspace</div>
          <h2 className="section-title">Three windows. One agent.</h2>
          <p className="section-sub">Code, Live Preview and Advisor — side by side. Switch tabs, never switch tools.</p>
          <div className="windows-grid">
            <div className="win-card" data-testid="win-code">
              <div className="win-chrome">
                <span className="win-dot r" /><span className="win-dot y" /><span className="win-dot g" />
                <span className="win-title">Code · auth.py</span>
                <span className="win-pill">DIFF +24 / −6</span>
              </div>
              <div className="win-body win-code">
                <span className="ln">12</span><span className="kw">def</span> <span className="fn">login</span>(email, pw):<br />
                <span className="ln">13</span>  <span className="cm"># Iter 184 — bcrypt + rate-limit</span><br />
                <span className="ln">14</span>  user = db.users.find_one({"{"}<span className="str">{'"email"'}</span>: email{"}"})<br />
                <span className="ln">15</span>  <span className="kw">if</span> <span className="kw">not</span> user:<br />
                <span className="ln">16</span>    <span className="kw">raise</span> <span className="fn">HTTPException</span>(<span className="str">401</span>)<br />
                <span className="ln">17</span>  <span className="kw">if</span> bcrypt.<span className="fn">checkpw</span>(pw, user[<span className="str">{'"hash"'}</span>]):<br />
                <span className="ln">18</span>    <span className="kw">return</span> <span className="fn">create_token</span>(user)<br />
                <span className="ln">19</span>  <span className="kw">raise</span> <span className="fn">HTTPException</span>(<span className="str">401</span>)
              </div>
            </div>
            <div className="win-card" data-testid="win-preview">
              <div className="win-chrome">
                <span className="win-dot r" /><span className="win-dot y" /><span className="win-dot g" />
                <span className="win-title">Live preview</span>
                <span className="win-pill">localhost:3000</span>
              </div>
              <div className="win-body win-preview">
                <span className="browser-bar">▸ https://auremcto.com/login</span>
                <span className="live-tag">● live</span>
                <div style={{ clear: "both", marginTop: 14 }}>
                  <h4>Welcome back</h4>
                  <p>Sign in to ship your next commit.</p>
                  <div className="preview-btn">Login →</div>
                  <div style={{ marginTop: 14, fontSize: 11, color: "var(--muted-3)", fontFamily: "var(--font-mono)" }}>
                    <span style={{ color: "#4ade80" }}>✓</span> hot reload · 142ms<br />
                    <span style={{ color: "#4ade80" }}>✓</span> tests pass · 38 / 38<br />
                    <span style={{ color: "#4ade80" }}>✓</span> lint clean
                  </div>
                </div>
              </div>
            </div>
            <div className="win-card" data-testid="win-advisor">
              <div className="win-chrome">
                <span className="win-dot r" /><span className="win-dot y" /><span className="win-dot g" />
                <span className="win-title">Ask advisor</span>
                <span className="win-pill">ORA · Pro</span>
              </div>
              <div className="win-body win-advisor">
                <div className="advisor-msg user">Why are we using bcrypt over argon2 here?</div>
                <div className="advisor-msg bot">
                  <b>ORA:</b> Your repo&apos;s been on bcrypt since <code>auth.py</code> was first written (Iter 04).
                  Argon2 is stronger but you&apos;d need a migration: re-hash on next login + add an
                  <code style={{ color: "var(--accent)" }}> algo</code> column. Want me to draft the PR?
                </div>
                <div className="advisor-msg user">Yes — and keep bcrypt as fallback for older rows.</div>
                <div className="advisor-msg bot">
                  <span className="advisor-typing"><span /><span /><span /></span>
                  planning across 3 files…
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ─── Modes ─── */}
        <section className="section">
          <div className="section-label">Modes</div>
          <h2 className="section-title">Pick your speed</h2>
          <p className="section-sub">Every mode commits direct to GitHub.</p>
          <div className="tabs-container" data-testid="modes-tabs">
            <div className="tabs-row">
              {MODES.map((m, i) => (
                <button
                  key={i}
                  type="button"
                  data-testid={`mode-tab-${i}`}
                  className={`tab-btn ${tab === i ? "active" : ""}`}
                  onClick={() => setTab(i)}
                >
                  {m.label}
                </button>
              ))}
            </div>
            <div className="tab-content">
              <div className="tab-info">
                <h3>{MODES[tab].title}</h3>
                <p>{MODES[tab].blurb}</p>
                <ul className="tab-features">
                  {MODES[tab].features.map((f, i) => <li key={i}>{f}</li>)}
                </ul>
              </div>
              <div className="terminal">{MODES[tab].terminal}</div>
            </div>
          </div>
        </section>

        {/* ─── Why teams switch ─── */}
        <section className="section" id="why-switch">
          <div className="section-label">Why teams switch</div>
          <h2 className="section-title">Built like a teammate, not a chat bot</h2>
          <p className="section-sub">Six things ORA does that the autocompletes don&apos;t. <b>All shipped, all in production.</b></p>
          <div className="teams-grid">
            {TEAMS.map((t, i) => (
              <div className="team-card" key={i}>
                <div className="team-icon">{t.icon}</div>
                <div className="team-tag">{t.tag}</div>
                <div className="team-title">{t.title}</div>
                <div className="team-desc">{t.desc}</div>
              </div>
            ))}
          </div>
        </section>

        {/* ─── Comparison ─── */}
        <section className="section">
          <div className="section-label">Comparison</div>
          <h2 className="section-title">Why developers choose ORA</h2>
          <p className="section-sub">No IDE. No token meters. No guessing. No vendor lock.</p>
          <div style={{ overflowX: "auto" }}>
            <table className="compare-table">
              <thead>
                <tr>
                  <th>Feature</th>
                  <th className="ora-col">ORA</th>
                  <th>GitHub Copilot</th>
                  <th>Cursor</th>
                  <th>Devin</th>
                  <th>Claude Code</th>
                </tr>
              </thead>
              <tbody>
                {COMPARE_ROWS.map(([feature, ...cells], i) => (
                  <tr key={i}>
                    <td className="feature-name">{feature}</td>
                    {cells.map(([cls, val], j) => (
                      <td key={j} className={j === 0 ? "ora-cell" : ""}>
                        <span className={cls === "yes" ? "yes" : "no"}>
                          {cls === "yes" ? "✓ " : (val.startsWith("$") || val === "All" || val === "Marketplace" || val === "Native" || val === "MIT extension" || val === "Built-in" || val === "Ollama / LM Studio" ? "" : "✗ ")}{val}
                        </span>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="compare-cta">
            <div className="live-dot" />
            <div className="compare-cta-text">
              <b>Try ORA live — free.</b> Connect your GitHub repo and ship your first task in under 2 minutes. No credit card required.
            </div>
            <Link className="btn-primary" to="/signup" data-testid="compare-cta-signup">Start free</Link>
          </div>
        </section>

        {/* ─── Reviews ─── */}
        <section className="section" id="reviews">
          <div className="section-label">Reviews</div>
          <h2 className="section-title">What developers are saying</h2>
          <p className="section-sub">Real feedback from real developers.</p>
          <div className="reviews-grid">
            {REVIEWS.map((r, i) => (
              <div className="review-card" key={i}>
                <div className="review-stars">{"★".repeat(r.stars)}</div>
                <div className="review-text">&quot;{r.text}&quot;</div>
                <div className="review-author">
                  <div className="review-avatar" style={r.color ? { background: `${r.color}33`, color: r.color } : null}>{r.initials}</div>
                  <div>
                    <div className="review-name">{r.name}</div>
                    <div className="review-role">{r.role}</div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ─── Pricing — real Stripe-wired component ─── */}
        <section className="section" id="pricing">
          <div className="section-label">Pricing</div>
          <h2 className="section-title">Flat pricing. No surprises.</h2>
          <p className="section-sub">No token billing. Same price whether you ship 5 or 500 tasks. Annual plans save 20%.</p>
          <PricingCards />
        </section>

        {/* ─── FAQ ─── */}
        <section className="section" id="faq">
          <div className="section-label">FAQ</div>
          <h2 className="section-title">Questions &amp; answers</h2>
          <p className="section-sub">Everything you need to know.</p>
          <div className="faq-list">
            {FAQS.map((f, i) => (
              <div className={`faq-item ${openFaq === i ? "open" : ""}`} key={i}>
                <div
                  className="faq-q"
                  data-testid={`faq-q-${i}`}
                  onClick={() => setOpenFaq(openFaq === i ? null : i)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setOpenFaq(openFaq === i ? null : i); }}
                >
                  <span className="faq-q-text">{f.q}</span>
                  <span className="faq-icon">+</span>
                </div>
                <div className="faq-a">{f.a}</div>
              </div>
            ))}
          </div>
        </section>

        {/* ─── CTA ─── */}
        <section className="cta-section">
          <h2 className="cta-title">Ready to ship code<span>without an IDE?</span></h2>
          <p className="cta-sub">Start free. 10 tasks. No credit card required.</p>
          <div className="hero-buttons">
            <Link className="btn-primary" to="/signup" data-testid="cta-signup">Start free — 10 tasks</Link>
            <Link className="btn-ghost" to="/wall" data-testid="cta-wall">See real ships</Link>
          </div>
        </section>

      </div>

      {/* ─── Footer ─── */}
      <footer className="footer" data-testid="ora-footer">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <img src="/ora-icon.png" alt="ORA" className="logo-img" />
          <div className="footer-text">ORA by Aurem CTO — Built for developers · MIT extension</div>
        </div>
        <div className="footer-links">
          <Link to="/privacy"        data-testid="footer-privacy">Privacy</Link>
          <Link to="/terms"          data-testid="footer-terms">Terms</Link>
          <Link to="/acceptable-use" data-testid="footer-aup">Acceptable use</Link>
          <Link to="/wall">Ship Wall</Link>
          <Link to="/login">Sign in</Link>
          <a   href="mailto:ora@auremcto.com" data-testid="footer-support">Contact: ora@auremcto.com</a>
        </div>
      </footer>
    </div>
  );
}
