/**
 * AskAdvisor.jsx — Iter 212m-81 — JSX port of v0 `ask-advisor.tsx`.
 */
import React, { useEffect, useRef, useState } from "react";
import { cn } from "./cn";
import { Lightbulb, ArrowUp, ChevronRight, AlertTriangle, GitPullRequest, BarChart2, Sparkles } from "lucide-react";

const seedMessages = [
  { id: "m1", role: "advisor",
    text: "Run throughput is up 12.4% this week. The Drizzle migration on data-layer failed twice — likely a schema drift. 3 PRs are awaiting your review." },
];

const CHIPS = [
  { icon: AlertTriangle,  label: "Diagnose failed run", danger: true },
  { icon: GitPullRequest, label: "Summarize open PRs",  danger: false },
  { icon: BarChart2,      label: "Token breakdown",      danger: false },
];

const CANNED =
  "Looking into run_5a3f now — it failed at schema validation. The `billing` table is missing a `currency` column that the new Drizzle model expects. I can open a migration PR with a safe default. Shall I proceed?";

export function AskAdvisor({ collapsed = false, onCollapse }) {
  const [messages, setMessages] = useState(seedMessages);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const scrollRef = useRef(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, thinking]);

  function send(text) {
    const t = (text || "").trim();
    if (!t) return;
    setMessages((p) => [...p, { id: `u${Date.now()}`, role: "user", text: t }]);
    setInput("");
    setThinking(true);
    setTimeout(() => {
      setThinking(false);
      setMessages((p) => [...p, { id: `a${Date.now()}`, role: "advisor", text: CANNED }]);
    }, 1000);
  }

  return (
    <div data-testid="ds2-advisor" className={cn(
      "relative hidden h-full shrink-0 overflow-visible xl:flex",
      "transition-[width] duration-200 ease-in-out",
      collapsed ? "w-0" : "w-[300px]",
    )}>
      <button onClick={() => onCollapse?.(false)} aria-label="Open Advisor panel"
        data-testid="ds2-advisor-open"
        className={cn(
          "absolute top-1/2 z-30 -translate-y-1/2 flex flex-col items-center gap-1.5 rounded-l-lg border border-r-0 border-border bg-card px-1.5 py-3 shadow-lg transition-all duration-200 ease-in-out hover:bg-secondary",
          collapsed ? "-left-7 opacity-100 pointer-events-auto" : "left-0 opacity-0 pointer-events-none",
        )}>
        <Sparkles className="size-3 text-primary" strokeWidth={2.5} />
        <span className="text-[9px] font-bold uppercase tracking-[0.12em] text-muted-foreground" style={{ writingMode: "vertical-rl" }}>
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
          <button onClick={() => onCollapse?.(true)} aria-label="Collapse Advisor panel" title="Collapse panel"
            data-testid="ds2-advisor-close"
            className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground">
            <ChevronRight className="size-4" strokeWidth={2} />
          </button>
        </div>

        <div className="mx-3 mt-3 shrink-0 rounded-lg border border-warning/20 bg-[#1a1200] p-3">
          <p className="mb-1 text-[11px] font-bold text-warning">Morning brief</p>
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            Run throughput +12.4% · Drizzle migration failed · 3 PRs need review
          </p>
        </div>

        <div className="mx-3 mt-3 shrink-0 flex flex-wrap gap-1.5">
          {CHIPS.map(({ icon: Icon, label, danger }) => (
            <button key={label} onClick={() => send(label)}
              className="flex items-center gap-1.5 rounded-full border border-border px-2.5 py-[5px] text-[11px] font-medium text-muted-foreground transition-colors hover:border-border/80 hover:text-foreground">
              <Icon className={cn("size-3 shrink-0", danger ? "text-destructive" : "text-primary")} strokeWidth={2.5} />
              {label}
            </button>
          ))}
        </div>

        <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3">
          {messages.map((m) =>
            m.role === "advisor" ? (
              <div key={m.id} className="flex items-start gap-2">
                <div className="mt-[2px] flex size-[22px] shrink-0 items-center justify-center rounded-full bg-primary/15 text-primary">
                  <Lightbulb className="size-3" strokeWidth={2.5} />
                </div>
                <div className="rounded-xl rounded-tl-sm bg-[#161616] px-3 py-2 text-[12px] leading-relaxed text-foreground/90">{m.text}</div>
              </div>
            ) : (
              <div key={m.id} className="flex justify-end">
                <div className="max-w-[85%] rounded-xl rounded-tr-sm bg-primary px-3 py-2 text-[12px] leading-relaxed text-primary-foreground">{m.text}</div>
              </div>
            ),
          )}
          {thinking && (
            <div className="flex items-start gap-2">
              <div className="mt-[2px] flex size-[22px] shrink-0 items-center justify-center rounded-full bg-primary/15 text-primary">
                <Lightbulb className="size-3" strokeWidth={2.5} />
              </div>
              <div className="flex items-center gap-[3px] rounded-xl rounded-tl-sm bg-[#161616] px-3 py-3">
                <span className="size-1.5 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:-0.3s]" />
                <span className="size-1.5 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:-0.15s]" />
                <span className="size-1.5 animate-bounce rounded-full bg-muted-foreground/60" />
              </div>
            </div>
          )}
        </div>

        <div className="shrink-0 border-t border-border p-3">
          <form onSubmit={(e) => { e.preventDefault(); send(input); }}
            className="rounded-xl border border-[#222222] bg-[#111111] px-3 py-2.5">
            <textarea value={input} onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
              rows={2} placeholder="Ask about runs, repos, or usage..."
              className="w-full resize-none bg-transparent text-[12px] text-foreground placeholder:text-muted-foreground/60 focus:outline-none" />
            <div className="flex justify-end pt-1">
              <button type="submit" disabled={!input.trim()} aria-label="Send message"
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
