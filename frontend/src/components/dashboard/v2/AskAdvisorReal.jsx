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
import { streamChat, api } from "../../../lib/api";
import { getActiveProjectId } from "../../TabBar";
import {
  Lightbulb, ArrowUp, ChevronRight, Square,
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
  // Iter 212m-207 — Founder request: Ask Advisor was stuck on
  // "thinking…" with no counter, no visual progress, and no way to
  // cancel.  Track elapsed ms since send() so the UI can show
  // "thinking · 12s" and auto-abort after a hard timeout if the SSE
  // stream stalls (network hiccup, backend hang, LLM slow-down).
  const [thinkingStartMs, setThinkingStartMs] = useState(null);
  const [thinkingElapsed, setThinkingElapsed] = useState(0);
  const timeoutRef = useRef(null);

  // Iter 212m-209 — Live project-scoped context (findings, council,
  // deploy-sync, quota).  Powers the dynamic morning-brief pill AND
  // is also injected server-side into the LLM system prompt.  We
  // refetch on project change so the pill can't stale.
  const [ctx, setCtx] = useState(null);
  useEffect(() => {
    let dead = false;
    if (!effectiveProjectId) { setCtx(null); return; }
    (async () => {
      try {
        const r = await api.get("/advisor/context", { params: { project_id: effectiveProjectId } });
        if (!dead) setCtx(r.data);
      } catch { if (!dead) setCtx(null); }
    })();
    return () => { dead = true; };
  }, [effectiveProjectId]);

  useEffect(() => {
    if (!thinkingStartMs) { setThinkingElapsed(0); return; }
    const id = setInterval(() => {
      setThinkingElapsed(Math.floor((Date.now() - thinkingStartMs) / 1000));
    }, 500);
    return () => clearInterval(id);
  }, [thinkingStartMs]);

  // Explicit stop button + safety timeout share this cleanup path.
  function stopThinking(reasonMsg) {
    try { abortRef.current?.abort(); } catch { /* noop */ }
    abortRef.current = null;
    if (timeoutRef.current) { clearTimeout(timeoutRef.current); timeoutRef.current = null; }
    setThinking(false);
    setThinkingStartMs(null);
    if (reasonMsg) {
      setMessages((p) => {
        const copy = p.slice();
        // Find the last advisor message and stamp the abort reason if
        // it's still empty (no partial content received yet).
        for (let i = copy.length - 1; i >= 0; i--) {
          if (copy[i].role === "advisor") {
            if (!copy[i].text) copy[i] = { ...copy[i], text: reasonMsg };
            break;
          }
        }
        return copy;
      });
    }
  }
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

  // Iter 212m-202 — Expose the advisor panel's live width to the rest
  // of the app via a CSS variable. Consumed by <Toaster/> so celebration
  // toasts don't stack on top of the panel when it's expanded. Only
  // applies at xl+ (the panel is `hidden` below xl). Kept as a body-
  // level var so any absolutely-positioned overlay can read it.
  useEffect(() => {
    const setVar = () => {
      const isXL = window.matchMedia("(min-width: 1280px)").matches;
      const w = isXL && !collapsed ? "300px" : "0px";
      document.documentElement.style.setProperty("--advisor-w", w);
    };
    setVar();
    window.addEventListener("resize", setVar);
    return () => {
      window.removeEventListener("resize", setVar);
      document.documentElement.style.removeProperty("--advisor-w");
    };
  }, [collapsed]);

  function send(text) {
    const t = (text || "").trim();
    if (!t || thinking) return;
    // Iter 212m-190 — PROJECT CONTEXT BUG FIX. If the parent Dashboard
    // has not finished hydrating `activeProject` yet (race between the
    // first paint and /cto/projects/list resolving), fall back to
    // TabBar's localStorage source of truth so the advisor never sends
    // project_id=null when a project is actually active. Result: no
    // more "No repo is connected right now" replies when the sidebar
    // and breadcrumb clearly show a connected repo.
    const effectiveProjectId = projectId || getActiveProjectId() || null;
    const userMsgId = `u${Date.now()}`;
    const advisorId = `a${Date.now()}`;
    setMessages((p) => [
      ...p,
      { id: userMsgId, role: "user", text: t },
      { id: advisorId, role: "advisor", text: "" },
    ]);
    setInput("");
    setThinking(true);
    setThinkingStartMs(Date.now());

    const ac = new AbortController();
    abortRef.current = ac;
    let assembled = "";

    // Iter 212m-207 — 90-second safety timeout so a stalled SSE stream
    // can't lock the advisor forever.  Auto-abort + surface an error
    // so the user can just retry.
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => {
      stopThinking("⚠ Advisor timed out after 90 s — please retry");
    }, 90_000);

    streamChat({
      prompt:     t,
      session_id: null,         // ephemeral
      project_id: effectiveProjectId,
      ora_panel:  true,         // <-- triggers the casual advisor voice
      signal:     ac.signal,
      onToken: (delta) => {
        if (!delta) return;
        // Reset the safety timer whenever we receive a token — if the
        // stream is producing output it's alive.
        if (timeoutRef.current) {
          clearTimeout(timeoutRef.current);
          timeoutRef.current = setTimeout(() => {
            stopThinking("⚠ Advisor stalled mid-stream — please retry");
          }, 45_000);
        }
        assembled += delta;
        setMessages((p) => {
          const copy = p.slice();
          const idx = copy.findIndex((m) => m.id === advisorId);
          if (idx >= 0) copy[idx] = { ...copy[idx], text: assembled };
          return copy;
        });
      },
      onDone: () => {
        setThinking(false);
        setThinkingStartMs(null);
        abortRef.current = null;
        if (timeoutRef.current) { clearTimeout(timeoutRef.current); timeoutRef.current = null; }
      },
      onError: (err) => {
        setThinking(false);
        setThinkingStartMs(null);
        abortRef.current = null;
        if (timeoutRef.current) { clearTimeout(timeoutRef.current); timeoutRef.current = null; }
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
        "flex h-full w-[300px] shrink-0 flex-col border-l border-border bg-[#0A0A0A]",
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

        <div className="mx-3 mt-3 shrink-0 rounded-lg border border-warning/20 bg-[#1a1200] p-3" data-testid="ds2-advisor-morning-brief">
          <p className="mb-1 text-[11px] font-bold text-warning">Morning brief</p>
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {ctx ? (
              <>
                <b className="text-foreground">{ctx.project_name}</b>
                {" · "}
                {ctx.findings?.error == null && ctx.findings?.total != null
                  ? <>{ctx.findings.total} open finding{ctx.findings.total === 1 ? "" : "s"}
                      {(ctx.findings.p0 || 0) > 0 ? <> ({ctx.findings.p0} P0)</> : null}
                    </>
                  : <>findings pata nahi</>}
                {" · Council A: "}
                {ctx.council?.live === true
                  ? <span className="text-green-500">live</span>
                  : ctx.council?.live === false
                    ? <span className="text-red-500">degraded</span>
                    : <>pata nahi</>}
                {" · Deploy: "}
                {ctx.deploy_sync?.in_sync === true
                  ? <span className="text-green-500">in-sync</span>
                  : ctx.deploy_sync?.in_sync === false
                    ? <span className="text-amber-500">out-of-sync</span>
                    : <>pata nahi</>}
                {ctx.quota?.tokens_used != null && ctx.quota?.tokens_limit != null
                  ? <> · Tokens: {ctx.quota.tokens_used}/{ctx.quota.tokens_limit}</>
                  : null}
              </>
            ) : (
              effectiveProjectId
                ? "loading live signals…"
                : "select a project to see live signals"
            )}
          </p>
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
          {/* Iter 212m-207 — Visible thinking indicator with an
              elapsed-seconds counter.  Renders while `thinking` is
              true AND the current advisor bubble is still empty
              (once tokens start streaming the bubble shows content
              instead). */}
          {thinking && (
            <div className="flex justify-start" data-testid="ds2-advisor-thinking">
              <div className="max-w-[85%] rounded-xl rounded-tl-sm border border-border bg-muted px-3 py-2 text-[12px] leading-relaxed text-muted-foreground flex items-center gap-2">
                <span className="inline-flex gap-1" aria-hidden="true">
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-bounce" style={{ animationDelay: "0ms" }} />
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-bounce" style={{ animationDelay: "140ms" }} />
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-bounce" style={{ animationDelay: "280ms" }} />
                </span>
                <span className="font-mono text-[11px]">
                  ORA is thinking · <span data-testid="ds2-advisor-thinking-counter">{thinkingElapsed}s</span>
                </span>
              </div>
            </div>
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
              {thinking ? (
                <button
                  type="button"
                  onClick={() => stopThinking("⏹ Cancelled by you")}
                  aria-label="Stop advisor"
                  data-testid="ds2-advisor-stop"
                  className="flex size-7 items-center justify-center rounded-full bg-red-500 text-white transition-opacity hover:opacity-90"
                  title="Stop"
                >
                  <Square className="size-3" strokeWidth={2.5} />
                </button>
              ) : (
                <button type="submit" disabled={!input.trim()}
                  aria-label="Send message"
                  data-testid="ds2-advisor-send"
                  className="flex size-7 items-center justify-center rounded-full bg-primary text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-30">
                  <ArrowUp className="size-3.5" strokeWidth={2.5} />
                </button>
              )}
            </div>
          </form>
        </div>
      </aside>
    </div>
  );
}
