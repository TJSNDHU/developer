/**
 * WhyOra.jsx — "Everything ORA does others don't" page.
 *
 * Iter 212m-219 — Standalone deep-dive page linked from the Landing
 * "3 killer features" section.  Renders all 20 uniquely-implemented
 * AUREM features grouped into 4 tiers with real file references so
 * a skeptical developer can grep and verify every claim.
 *
 * Design language matches Landing.jsx (dark base + amber accent +
 * IBM Plex Mono headers + hairline dividers). Cards are compact and
 * mobile-friendly (grid collapses to 1-col below 640 px).
 *
 * Route: /why-ora    (wired in App.jsx)
 */
import React from "react";
import { Link } from "react-router-dom";

const CSS = `
.wo {
  --bg:        #0a0e1a;
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
    radial-gradient(900px 540px at 18% -8%,  rgba(245,158,11,0.18), transparent 70%),
    radial-gradient(820px 480px at 86% 6%,   rgba(99,102,241,0.12), transparent 65%),
    linear-gradient(180deg, rgba(10,14,26,0.80) 0%, rgba(5,8,17,0.94) 100%),
    #050811;
  background-attachment: fixed;
  min-height: 100vh;
  font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
}
.wo * { box-sizing: border-box; }
.wo .wrap { max-width: 1180px; margin: 0 auto; padding: 0 24px; }

/* Top nav */
.wo .nav {
  position: sticky; top: 0; z-index: 50;
  backdrop-filter: blur(18px);
  background: rgba(10,14,26,0.72);
  border-bottom: 1px solid var(--line);
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 32px;
}
.wo .nav a.logo { color: var(--accent); font-family: var(--font-mono); font-weight: 800; font-size: 22px; letter-spacing: -1px; text-decoration: none; }
.wo .nav a.logo span { color: var(--muted-2); font-weight: 400; font-size: 13px; margin-left: 6px; letter-spacing: 0; }
.wo .nav .back { color: var(--muted-1); font-family: var(--font-mono); font-size: 13px; text-decoration: none; }
.wo .nav .back:hover { color: var(--accent); }

/* Hero */
.wo .hero { padding: 96px 24px 40px; text-align: center; }
.wo .hero .kicker {
  display: inline-block; font-family: var(--font-mono); font-size: 11px;
  text-transform: uppercase; letter-spacing: 1.8px;
  background: var(--accent-bg); color: var(--accent);
  padding: 7px 14px; border: 1px solid var(--accent-br);
  border-radius: 999px; margin-bottom: 28px;
}
.wo .hero h1 {
  font-family: var(--font-mono); font-weight: 700;
  font-size: clamp(30px, 4.8vw, 52px);
  line-height: 1.14; letter-spacing: -1.4px;
  margin: 0 auto; max-width: 900px;
}
.wo .hero h1 em { font-style: normal; color: var(--accent); }
.wo .hero p {
  color: var(--muted-1); font-size: clamp(15px, 1.3vw, 17px);
  line-height: 1.7; margin: 30px auto 0; max-width: 720px;
}

/* Tier headers */
.wo .tier { padding: 64px 24px 24px; }
.wo .tier-head { display: flex; align-items: baseline; justify-content: space-between; border-bottom: 1px solid var(--line); padding-bottom: 18px; margin-bottom: 34px; flex-wrap: wrap; gap: 12px; }
.wo .tier-num { font-family: var(--font-mono); color: var(--accent); font-size: 11px; text-transform: uppercase; letter-spacing: 1.8px; }
.wo .tier-title { font-family: var(--font-mono); font-weight: 700; font-size: clamp(22px, 2.8vw, 32px); letter-spacing: -0.8px; margin: 6px 0 0 0; }
.wo .tier-note { color: var(--muted-2); font-family: var(--font-mono); font-size: 12px; }

/* Feature grid */
.wo .grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
}
.wo .card {
  background: rgba(255,255,255,0.015);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 22px;
  display: flex; flex-direction: column;
  transition: border-color 0.18s, background 0.18s, transform 0.12s;
  position: relative;
}
.wo .card:hover { border-color: var(--accent-br); background: rgba(245,158,11,0.025); transform: translateY(-1px); }
.wo .card .num {
  position: absolute; top: 14px; right: 16px;
  font-family: var(--font-mono); font-size: 11px; color: var(--muted-3);
}
.wo .card .icon {
  font-family: var(--font-mono); color: var(--accent);
  font-size: 20px; font-weight: 700; letter-spacing: -0.5px;
  margin-bottom: 12px;
}
.wo .card h3 {
  font-family: var(--font-mono); font-size: 15px; font-weight: 700;
  margin: 0 0 8px 0; color: var(--text); letter-spacing: -0.3px;
  line-height: 1.35;
}
.wo .card p {
  color: var(--muted-1); font-size: 13.5px; line-height: 1.6;
  margin: 0 0 14px 0; flex: 1;
}
.wo .card .proof {
  display: inline-flex; align-items: center; gap: 6px;
  font-family: var(--font-mono); font-size: 11px;
  color: var(--muted-2);
  padding: 5px 10px; border: 1px solid var(--line);
  border-radius: 6px; align-self: flex-start;
  background: rgba(0,0,0,0.25);
}
.wo .card .proof::before { content: "📁"; filter: grayscale(1) opacity(0.6); font-size: 10px; }

/* CTA */
.wo .cta { padding: 96px 24px; text-align: center; border-top: 1px solid var(--line); margin-top: 48px; }
.wo .cta h2 { font-family: var(--font-mono); font-size: clamp(22px, 3vw, 32px); letter-spacing: -0.8px; margin: 0 0 12px 0; }
.wo .cta p { color: var(--muted-1); margin-bottom: 32px; max-width: 520px; margin-left: auto; margin-right: auto; }
.wo .cta .row { display: flex; gap: 14px; justify-content: center; flex-wrap: wrap; }
.wo .cta .btn-primary, .wo .cta .btn-ghost {
  font-family: var(--font-mono); font-size: 14px; padding: 14px 28px;
  border-radius: 10px; text-decoration: none; font-weight: 600;
  transition: opacity 0.15s, transform 0.08s;
}
.wo .cta .btn-primary { background: var(--accent); color: #000; }
.wo .cta .btn-primary:hover { opacity: 0.92; }
.wo .cta .btn-ghost { background: transparent; color: var(--muted-1); border: 1px solid var(--line); }
.wo .cta .btn-ghost:hover { color: var(--text); border-color: var(--muted-3); }
.wo .cta .btn-primary:active, .wo .cta .btn-ghost:active { transform: translateY(1px); }

/* Mobile */
@media (max-width: 640px) {
  .wo .nav { padding: 12px 20px; }
  .wo .hero { padding: 64px 20px 20px; }
  .wo .tier { padding: 48px 20px 20px; }
  .wo .card { padding: 18px; }
}
`;


// ── The 20 features, grouped ────────────────────────────────────────
// Every card carries: number, tag (small mono glyph), title, hook
// (max ~120 chars), and `proof` (real file path that a skeptic can
// grep to verify the claim).
const TIERS = [
  {
    num:   "Tier 1",
    title: "The moats nobody else has",
    note:  "5 features — verified by code, not marketing",
    items: [
      {
        n: "01",
        icon: "SHIP",
        h: "Full-Scan Ship-Block + 3× Auto-Heal",
        p: "Loop cannot commit until Vanguard + Bug Hunt + Docker CIS + HTTP headers pass on the files ORA just wrote. On failure — auto-heal up to 3 times, then pause-for-user.",
        proof: "services/loop_full_scan.py",
      },
      {
        n: "02",
        icon: "VOTE",
        h: "Parliament — Multi-Model Council with CEO Picker",
        p: "Three council members generate in parallel at temps 0.1 / 0.2 / 0.3. CEO (different model) picks best. Circuit breaker + trace_id + Semaphore(6).",
        proof: "core/parliament.py",
      },
      {
        n: "03",
        icon: "GUARD",
        h: "Vanguard Verify Agent",
        p: "Anthropic's defending-code-reference-harness pattern. Separate model with different prompt re-reviews every patch for 25 vulnerability patterns before commit.",
        proof: "services/vanguard_verify_agent.py",
      },
      {
        n: "04",
        icon: "CITE",
        h: "Citation Guard — cannot hallucinate file contents",
        p: "If ORA mentions a file path or version WITHOUT a read_repo_file tool call in the same turn — response is auto-blocked, files fetched, LLM re-run with real data.",
        proof: "services/citation_guard.py",
      },
      {
        n: "05",
        icon: "LEARN",
        h: "ORA Council Retriever — self-learn at N=165",
        p: "Uses (user_message, final_output) pairs as retrieval-augmented few-shots RIGHT NOW instead of waiting 1,000 samples for fine-tuning. Faster, free, always-fresh.",
        proof: "services/ora_council_retriever.py",
      },
    ],
  },
  {
    num:   "Tier 2",
    title: "Zero-LLM static intelligence",
    note:  "5 features — cost + speed moat",
    items: [
      {
        n: "06",
        icon: "SCAN",
        h: "5-Category Codebase Health Scanner",
        p: "Pure regex + AST. Security + perf + code quality + deps + database — all 5 for the price of one GitHub tree walk. LLM pays only when you click Fix.",
        proof: "routers/codebase_health.py",
      },
      {
        n: "07",
        icon: "BUG",
        h: "Bug Hunt — 50+ Nuclei-adapted static rules",
        p: "Adapted ProjectDiscovery's Nuclei HTTP templates into static source detectors. Catches at commit-time instead of runtime.",
        proof: "services/bug_hunt_rules.py",
      },
      {
        n: "08",
        icon: "LINT",
        h: "Design Linter — visual anti-patterns blocked",
        p: "Rejects transition:all, emoji icons in source, !important abuse, hardcoded colors without CSS vars, console.log, missing React key= props.",
        proof: "services/design_linter.py",
      },
      {
        n: "09",
        icon: "AST",
        h: "Architecture Health with radon",
        p: "Cyclomatic complexity > 10 flag, god files, circular import SCC detection, module boundary violations (routers importing routers). Pure AST, no LLM.",
        proof: "services/architecture_health.py",
      },
      {
        n: "10",
        icon: "POST",
        h: "Post-Task Scanner (after every commit)",
        p: "The moment ORA finishes a task, scans only changed files for secrets + broken imports. Max 3 findings. Zero LLM cost.",
        proof: "services/post_task_scanner.py",
      },
    ],
  },
  {
    num:   "Tier 3",
    title: "UX innovations",
    note:  "5 features — what makes ORA feel alive",
    items: [
      {
        n: "11",
        icon: "SEE",
        h: "Ask Advisor with Live Visual Context",
        p: "html2canvas captures your current screen → Gemini 2.5 Flash vision → advisor literally sees what you see. No screenshot upload UX; automatic.",
        proof: "services/advisor_vision.py",
      },
      {
        n: "12",
        icon: "MAP",
        h: "Mermaid GitDiagram — layered architecture",
        p: "LLM generates Mermaid.js flowchart with layer subgraphs from your actual import graph (not directory tree). Cached by tree_sha, instant on repeat.",
        proof: "services/mermaid_diagram.py",
      },
      {
        n: "13",
        icon: "F12",
        h: "F12 Error Bridge → Mode D Debugger",
        p: "Frontend snippet pipes console errors, network 4xx/5xx and stack traces into ORA with exact file+line mapping. Known errors take regex fast-path, zero LLM.",
        proof: "services/mode_d_debugger.py",
      },
      {
        n: "14",
        icon: "HEAL",
        h: "Repo Auto-Heal (sidebar dot self-recovers)",
        p: "Cheapest-first strategy pyramid: 3× exponential retry → PAT re-decrypt → OAuth refresh → surface. Transient failures never touch the user.",
        proof: "services/repo_heal.py",
      },
      {
        n: "15",
        icon: "RATE",
        h: "Rate-Limit Countdown Toast with Auto-Retry",
        p: "/scan hits GitHub rate limit → persistent countdown toast → auto-retries at 0 with 3-cycle cap + Cancel button. No dead-end error screens.",
        proof: "frontend/src/components/Toast.jsx",
      },
    ],
  },
  {
    num:   "Tier 4",
    title: "Cultural & cost innovations",
    note:  "5 features — the unfair advantages",
    items: [
      {
        n: "16",
        icon: "हिं",
        h: "Error Translator — Hinglish output",
        p: "Static catalog for ~20 common failures returns plain Hinglish + concrete step list. LLM fallback (Claude Haiku, 200 tokens) for unknowns.",
        proof: "services/error_translator.py",
      },
      {
        n: "17",
        icon: "KEY",
        h: "Universal LLM Key across OpenAI / Claude / Gemini",
        p: "One EMERGENT_LLM_KEY routes to any model. No juggling three separate API keys. Auto top-up baked in.",
        proof: "services/llm.py",
      },
      {
        n: "18",
        icon: "MODE",
        h: "Six-Mode Classifier with Confidence Score",
        p: "A casual · B council · C code write · D debug flow · E full-repo audit · F market engage. Confidence < 0.55 asks the user to disambiguate.",
        proof: "services/mode_classifier.py",
      },
      {
        n: "19",
        icon: "CACHE",
        h: "Cross-Pod Scan Cache Dedup",
        p: "owner/repo@tree_sha keyed in Redis. Same repo scanned by two users? Second one skips all 50-600 GitHub API calls. Compounds as user base grows.",
        proof: "services/scan_cache.py",
      },
      {
        n: "20",
        icon: "SIGN",
        h: "Real-Developer Commit Identity + Co-authored-by",
        p: "Commits attribute to real dev (from GitHub OAuth), Conventional Commits format, Co-authored-by: ORA trailer. Locked with 39 pytests.",
        proof: "services/git_identity.py",
      },
    ],
  },
];


export default function WhyOra() {
  return (
    <div className="wo" data-testid="why-ora-page">
      <style>{CSS}</style>

      {/* ── Top nav ─────────────────────────────────────────────── */}
      <nav className="nav">
        <Link to="/" className="logo" data-testid="why-ora-logo">
          AUREM<span>· CTO</span>
        </Link>
        <Link to="/" className="back" data-testid="why-ora-back">
          ← Back to home
        </Link>
      </nav>

      {/* ── Hero ────────────────────────────────────────────────── */}
      <section className="hero">
        <span className="kicker">Everything ORA does others don&apos;t</span>
        <h1>
          20 features that <em>literally live in the code</em> —
          grep them if you don&apos;t believe us.
        </h1>
        <p>
          Every claim below is backed by a real file path in the AUREM
          repo. This is not a marketing page — it&apos;s a technical
          inventory of the moats we&apos;ve built while Cursor, Devin,
          Copilot, Windsurf and Aider were doing something else.
        </p>
      </section>

      {/* ── The tiers ───────────────────────────────────────────── */}
      <div className="wrap">
        {TIERS.map((tier, ti) => (
          <section
            className="tier"
            key={tier.num}
            data-testid={`tier-${ti + 1}`}
          >
            <div className="tier-head">
              <div>
                <div className="tier-num">{tier.num}</div>
                <h2 className="tier-title">{tier.title}</h2>
              </div>
              <div className="tier-note">{tier.note}</div>
            </div>
            <div className="grid">
              {tier.items.map((it) => (
                <div
                  className="card"
                  key={it.n}
                  data-testid={`feature-card-${it.n}`}
                >
                  <span className="num">{it.n}</span>
                  <div className="icon">{it.icon}</div>
                  <h3>{it.h}</h3>
                  <p>{it.p}</p>
                  <code className="proof" title={`Grep the repo: ${it.proof}`}>
                    {it.proof}
                  </code>
                </div>
              ))}
            </div>
          </section>
        ))}
      </div>

      {/* ── CTA ─────────────────────────────────────────────────── */}
      <section className="cta">
        <h2>Now try the thing that built itself.</h2>
        <p>
          10 free tasks. No credit card. Connect your repo, describe
          what you want, watch ORA ship — with all 20 of the above
          running in the background.
        </p>
        <div className="row">
          <Link to="/signup" className="btn-primary" data-testid="why-ora-cta-signup">
            Start free →
          </Link>
          <Link to="/" className="btn-ghost" data-testid="why-ora-cta-home">
            Back to home
          </Link>
        </div>
      </section>
    </div>
  );
}
