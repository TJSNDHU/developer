/**
 * AskAdvisorReal.jsx — Iter 212m-83
 *
 * Real ORA Ask Advisor side panel for Dashboard v2.
 *
 * Wires the v0 visual layout to the REAL `streamChat()` SSE endpoint
 * with `ora_panel: true` so the user gets a true ORA reply with
 * Council few-shot retrieval, mode-routing, and fallback chains.
 *
 * Replaces the legacy <FloatingORAButton /> on the dashboard route.
 *
 * Behaviour:
 *   • Collapsed by default — vertical ADVISOR tab when closed.
 *   • Click `Diagnose failed run` / `Summarize open PRs` chips to
 *     auto-populate + send.
 *   • Enter to send, Shift+Enter for newline, ESC to collapse.
 *   • Per-session messages (not persisted across reloads — the panel
 *     is for ephemeral advice, not chat history).
 */
import React, { useEffect, useRef, useState } from "react";
import { cn } from "./cn";
import { streamChat } from "../../../lib/api";
import {
  Lightbulb, ArrowUp, ChevronRight,
  AlertTriangle, GitPullRequest, BarChart2, Sparkles,
} from "lucide-react";

const CHIPS = [
  { icon: AlertTriangle,  label: "Diagnose failed run", danger: true },
  { icon: GitPullRequest, label: "Summarize open PRs",  danger: false },
  { icon: BarChart2,      label: "Token breakdown",      danger: false },
];

export default function AskAdvisorReal({ collapsed = false, onCollapse, projectId }) {
  const [messages, setMessages] = useState([
    { id: "m0", role: "advisor",
      text: "Hi — I'm the ORA Advisor. Ask anything about your repo, "
            + "failed runs, PRs, or token usage. I'll respond live." },
  ]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const abortRef = useRef(null);
  const scrollRef = useRef(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight, behavior: "smooth",
    });
  }, [messages, thinking]);

  // ESC to collapse — convenience binding.
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !collapsed) onCollapse?.(true); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [collapsed, onCollapse]);

  function send(text) {
    const t = (text || "").trim();
    if (!t || thinking) return;
    const userMsgId = `u${Date.now()}`;
    const advisorId = `a${Date.now()}`;
    setMessages((p) => [
      ...p,
      { id: userMsgId, role: "user", text: t },
      { id: advisorId, role: "advisor", text: "" },
    ]);
    setInput("");
    setThinking(true);

    const ac = new AbortController();
    abortRef.current = ac;
    let assembled = "";

    streamChat({
      prompt:     t,
      session_id: null,         // ephemeral
      project_id: projectId || null,
      ora_panel:  true,         // <-- triggers the casual advisor voice
      signal:     ac.signal,
      onToken: (delta) => {
        if (!delta) return;
        assembled += delta;
        setMessages((p) => {
          const copy = p.slice();
          const idx = copy.findIndex((m) => m.id === advisorId);
          if (idx >= 0) copy[idx] = { ...copy[idx], text: assembled };
          return copy;
        });
      },
      onDone: () => { setThinking(false); abortRef.current = null; },
      onError: (err) => {
        setThinking(false); abortRef.current = null;
        setMessages((p) => {
          const copy = p.slice();
          const idx = copy.findIndex((m) => m.id === advisorId);
          const msg = err?.message || "(connection error — retry)";
          if (idx >= 0) copy[idx] = { ...copy[idx], text: `⚠ ${msg}` };
          return copy;
        });
      },
    });
  }

  return (
    <div data-testid="ds2-advisor-real" className={cn(
      "relative hidden h-full shrink-0 overflow-visible xl:flex",
      "transition-[width] duration-200 ease-in-out",
      collapsed ? "w-0" : "w-[300px]",
    )}>
      {/* Collapsed-state vertical "ADVISOR" tab */}
      <button onClick={() => onCollapse?.(false)} aria-label="Open Advisor panel"
        data-testid="ds2-advisor-open"
        className={cn(
          "absolute top-1/2 z-30 -translate-y-1/2 flex flex-col items-center gap-1.5 rounded-l-lg border border-r-0 border-border bg-card px-1.5 py-3 shadow-lg transition-all duration-200 ease-in-out hover:bg-secondary",
          collapsed ? "-left-7 opacity-100 pointer-events-auto"
                    : "left-0 opacity-0 pointer-events-none",
        )}>
        <Sparkles className="size-3 text-primary" strokeWidth={2.5} />
        <span className="text-[9px] font-bold uppercase tracking-[0.12em] text-muted-foreground"
              style={{ writingMode: "vertical-rl" }}>
          Advisor
        </span>
      </button>

      <aside className={cn(
        "flex h-full w-[300px] shrink-0 flex-col border-l border-border bg-[#0c0c0c]",
        "transition-transform duration-200 ease-in-out",
        collapsed ? "translate-x-full" : "translate-x-0",
      )}>
        <div className="flex h-[52px] shrink-0 items-center justify-between border-b border-border px-4">
          <div className="flex items-center gap-2.5">
            <div className="relative flex size-7 items-center justify-center rounded-lg bg-primary/15 text-primary">
              <Lightbulb className="size-3.5" strokeWidth={2.5} />
              <span className="absolute -right-px -top-px size-[7px] rounded-full border-2 border-[#0c0c0c] bg-success" />
            </div>
            <div className="leading-none">
              <p className="text-[13px] font-bold text-foreground">Ask Advisor</p>
              <p className="mt-[3px] text-[10px] text-muted-foreground">ORA copilot · online</p>
            </div>
          </div>
          <button onClick={() => onCollapse?.(true)} aria-label="Collapse Advisor panel"
            title="Collapse panel"
            data-testid="ds2-advisor-close"
            className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground">
            <ChevronRight className="size-4" strokeWidth={2} />
          </button>
        </div>

        <div className="mx-3 mt-3 shrink-0 flex flex-wrap gap-1.5">
          {CHIPS.map(({ icon: Icon, label, danger }) => (
            <button key={label} onClick={() => send(label)}
              data-testid={`ds2-advisor-chip-${label.toLowerCase().replace(/[^a-z0-9]/g, "-")}`}
              className="flex items-center gap-1.5 rounded-full border border-border px-2.5 py-[5px] text-[11px] font-medium text-muted-foreground transition-colors hover:border-border/80 hover:text-foreground">
              <Icon className={cn("size-3 shrink-0",
                danger ? "text-destructive" : "text-primary")} strokeWidth={2.5} />
              {label}
            </button>
          ))}
        </div>

        <div ref={scrollRef} data-testid="ds2-advisor-scroll"
          className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3">
          {messages.map((m) =>
            m.role === "advisor" ? (
              <div key={m.id} className="flex items-start gap-2">
                <div className="mt-[2px] flex size-[22px] shrink-0 items-center justify-center rounded-full bg-primary/15 text-primary">
                  <Lightbulb className="size-3" strokeWidth={2.5} />
                </div>
                <div className="rounded-xl rounded-tl-sm bg-[#161616] px-3 py-2 text-[12px] leading-relaxed text-foreground/90 whitespace-pre-wrap">
                  {m.text || (
                    <span className="inline-flex items-center gap-[3px]">
                      <span className="size-1.5 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:-0.3s]" />
                      <span className="size-1.5 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:-0.15s]" />
                      <span className="size-1.5 animate-bounce rounded-full bg-muted-foreground/60" />
                    </span>
                  )}
                </div>
              </div>
            ) : (
              <div key={m.id} className="flex justify-end">
                <div className="max-w-[85%] rounded-xl rounded-tr-sm bg-primary px-3 py-2 text-[12px] leading-relaxed text-primary-foreground whitespace-pre-wrap">
                  {m.text}
                </div>
              </div>
            ),
          )}
        </div>

        <div className="shrink-0 border-t border-border p-3">
          <form onSubmit={(e) => { e.preventDefault(); send(input); }}
            className="rounded-xl border border-[#222222] bg-[#111111] px-3 py-2.5">
            <textarea value={input} onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault(); send(input);
                }
              }}
              rows={2}
              placeholder={thinking ? "ORA is thinking…" : "Ask about runs, repos, or usage..."}
              disabled={thinking}
              data-testid="ds2-advisor-input"
              className="w-full resize-none bg-transparent text-[12px] text-foreground placeholder:text-muted-foreground/60 focus:outline-none disabled:opacity-60" />
            <div className="flex justify-end pt-1">
              <button type="submit" disabled={!input.trim() || thinking}
                aria-label="Send message"
                data-testid="ds2-advisor-send"
                className="flex size-7 items-center justify-center rounded-full bg-primary text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-30">
                <ArrowUp className="size-3.5" strokeWidth={2.5} />
              </button>
            </div>
          </form>
        </div>
      </aside>
    </div>
  );
}
