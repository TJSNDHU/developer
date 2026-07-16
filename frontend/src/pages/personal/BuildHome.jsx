/**
 * pages/personal/BuildHome.jsx — Iter 212m-235 — Phase 6
 *
 * Single-prompt landing for Personal Track. User types their idea,
 * clicks build, backend scaffolds a draft, then we route to the
 * Draft Review screen.
 */
import React, { useState, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { ArrowRight, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { PersonalShell, PrimaryButton } from "./_shell";
import { api } from "../../lib/api";

const EXAMPLES = [
  "A habit tracker with streak notifications",
  "A weekly meal planner with a shopping list",
  "A private journal that summarises my week",
  "A book club where friends vote on next month's book",
  "A workout log I can share with a coach",
];

export default function BuildHome() {
  const nav = useNavigate();
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy]     = useState(false);
  const [typing, setTyping] = useState(false);
  const taRef = useRef(null);

  useEffect(() => { taRef.current?.focus(); }, []);

  async function submit() {
    const brief = prompt.trim();
    if (brief.length < 10) {
      toast.error("Tell us a bit more — at least a sentence.");
      return;
    }
    setBusy(true);
    try {
      const r = await api.post("/scaffold/new-project", { brief });
      const draftId = r.data.draft_id;
      nav(`/build/${draftId}`);
    } catch (e) {
      toast.error("Hmm, something got tangled up. Let's try that again.");
      setBusy(false);
    }
  }

  /** Simulate typing the example chip into the textarea. */
  function typeExample(text) {
    if (busy || typing) return;
    setTyping(true); setPrompt("");
    let i = 0;
    const iv = setInterval(() => {
      i += 1;
      setPrompt(text.slice(0, i));
      if (i >= text.length) {
        clearInterval(iv); setTyping(false);
        taRef.current?.focus();
      }
    }, 22);
  }

  return (
    <PersonalShell>
      <div
        data-testid="build-home-page"
        style={{
          maxWidth: 780, margin: "0 auto",
          padding: "80px 24px 96px",
          textAlign: "center",
        }}
      >
        <motion.div
          initial={{ opacity: 0, y: 24 }} animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
        >
          <div style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "6px 12px", borderRadius: 999,
            background: "rgba(224,122,95,0.10)", color: "#D56A4F",
            fontSize: 12, fontWeight: 600, letterSpacing: "0.02em",
            marginBottom: 20,
          }}>
            <Sparkles size={12} /> Personal Track
          </div>
          <h1 style={{
            fontSize: "clamp(36px, 6vw, 56px)",
            fontWeight: 500, letterSpacing: "-0.03em",
            lineHeight: 1.05,
            fontFamily: "'Cabinet Grotesk', 'Manrope', sans-serif",
            margin: "0 0 20px",
          }}>
            What do you want<br/>to build?
          </h1>
          <p style={{
            fontSize: 17, color: "#6B6B63", lineHeight: 1.6,
            margin: "0 0 44px",
          }}>
            Describe your idea in plain English. We&apos;ll create the code,
            set up the database, and put it live on the internet.
          </p>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 30 }} animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.15 }}
          style={{
            background: "#FFFFFF",
            borderRadius: 24,
            border: "1px solid #E5E5DF",
            boxShadow: "0 12px 40px rgba(28,28,25,0.06), 0 2px 6px rgba(28,28,25,0.03)",
            padding: 20, textAlign: "left", position: "relative",
          }}
        >
          <textarea
            ref={taRef}
            data-testid="home-prompt-input"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submit();
            }}
            placeholder="e.g. A place where I can save recipes and share them with my sister…"
            disabled={busy}
            rows={4}
            style={{
              width: "100%", resize: "none",
              border: "none", outline: "none",
              fontSize: 18, lineHeight: 1.5,
              fontFamily: "inherit", color: "#1C1C19",
              background: "transparent",
              padding: "8px 6px 60px",
            }}
          />
          <div style={{
            position: "absolute", right: 20, bottom: 20,
          }}>
            <PrimaryButton
              data-testid="home-prompt-submit-button"
              onClick={submit}
              disabled={busy || prompt.trim().length < 10}
              style={{ padding: "10px 18px" }}
            >
              {busy ? "Building…" : "Build"} <ArrowRight size={16} />
            </PrimaryButton>
          </div>
        </motion.div>

        {/* Example chips */}
        <motion.div
          initial={{ opacity: 0 }} animate={{ opacity: 1 }}
          transition={{ duration: 0.6, delay: 0.35 }}
          style={{
            marginTop: 28, display: "flex", flexWrap: "wrap",
            gap: 8, justifyContent: "center",
          }}
        >
          {EXAMPLES.map((ex, i) => (
            <button
              key={i}
              data-testid="home-example-chip"
              disabled={busy || typing}
              onClick={() => typeExample(ex)}
              style={{
                padding: "8px 14px", borderRadius: 999,
                background: "#F4F3EE",
                border: "1px solid transparent",
                color: "#6B6B63", fontSize: 13,
                fontFamily: "inherit", cursor: (busy || typing) ? "wait" : "pointer",
                transition: "background 200ms ease, color 200ms ease",
              }}
              onMouseEnter={(e) => {
                if (busy || typing) return;
                e.currentTarget.style.background = "#EDECE5";
                e.currentTarget.style.color = "#1C1C19";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "#F4F3EE";
                e.currentTarget.style.color = "#6B6B63";
              }}
            >
              {ex}
            </button>
          ))}
        </motion.div>

        <p style={{
          marginTop: 44, fontSize: 12, color: "#8B8B7D",
        }}>
          <kbd style={{
            padding: "2px 6px", borderRadius: 4,
            background: "#F4F3EE", border: "1px solid #E5E5DF",
            fontFamily: "ui-monospace, monospace", fontSize: 11,
          }}>⌘ ↵</kbd> to build
        </p>
      </div>
    </PersonalShell>
  );
}
