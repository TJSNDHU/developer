/**
 * ChatView.jsx — Iter 212m-81 — JSX port of v0 `chat-view.tsx`.
 * Preview-only visual mock; the real production wiring (SSE streams,
 * Vanguard scan, ORA Council, etc.) stays in `ChatPanel.jsx` until
 * the user signs off on this look.
 */
import React, { useRef, useState } from "react";
import { cn } from "./cn";
import { diffLines } from "./dashboard-data";
import {
  ArrowUp, Check, CheckCircle2, Circle, FileCode2, GitPullRequest,
  Loader2, RefreshCw, ShieldAlert, GitBranch, Paperclip, BarChart2, Zap,
} from "lucide-react";
import ScanStatusStrip, { markScanJustCompleted } from "../../ScanStatusStrip";
import SlashCommandMenu, { matchSlashCommands, SLASH_COMMANDS } from "../../SlashCommandMenu";
import { getActiveProjectId, useActiveProject } from "../../TabBar";
import { api } from "../../../lib/api";

const LOOP_STEPS = [
  { label: "PLAN",    status: "done" },
  { label: "EXECUTE", status: "active" },
  { label: "VERIFY",  status: "pending" },
  { label: "SCAN",    status: "pending" },
  { label: "SHIP",    status: "pending" },
];

function LoopBar() {
  return (
    <div className="mx-auto flex w-full max-w-3xl items-center gap-2 rounded-lg border border-border bg-[#0e0e0e] px-4 py-2.5">
      <span className="mr-1 text-[10px] font-bold uppercase tracking-widest text-muted-foreground">Loop</span>
      {LOOP_STEPS.map((step, i) => (
        <div key={step.label} className="flex items-center gap-2">
          {i > 0 && (
            <div className={cn("h-px w-6 flex-shrink-0",
              LOOP_STEPS[i - 1].status === "done" ? "bg-success/60" : "bg-border")} />
          )}
          <div className="flex items-center gap-1.5">
            {step.status === "done"    && <CheckCircle2 className="size-3.5 text-success" strokeWidth={2.5} />}
            {step.status === "active"  && <Loader2 className="size-3.5 animate-loop-spin text-primary" strokeWidth={2.5} />}
            {step.status === "pending" && <Circle className="size-3.5 text-border" strokeWidth={2} />}
            <span className={cn("text-[10px] font-semibold uppercase tracking-wide",
              step.status === "done"    && "text-success",
              step.status === "active"  && "text-primary",
              step.status === "pending" && "text-muted-foreground/50",
            )}>{step.label}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function StreamHealthPill({ visible }) {
  if (!visible) return null;
  return (
    <div className="mx-auto flex w-full max-w-3xl items-center justify-between rounded-lg border border-warning/30 bg-warning/5 px-4 py-2">
      <div className="flex items-center gap-2 text-[12px] text-warning">
        <Zap className="size-3.5 shrink-0" strokeWidth={2.5} />
        <span className="font-semibold">Slow response</span>
        <span className="text-muted-foreground">· 34s silent · auto-retry in 56s</span>
      </div>
      <button className="text-[11px] font-medium text-muted-foreground transition-colors hover:text-foreground">Retry now</button>
    </div>
  );
}

function DiffBlock() {
  const adds = diffLines.filter((l) => l.type === "add").length;
  const dels = diffLines.filter((l) => l.type === "del").length;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-[#0a0a0a]">
      <div className="flex items-center justify-between border-b border-border bg-[#111111] px-3 py-2">
        <div className="flex items-center gap-2">
          <FileCode2 className="size-3.5 text-muted-foreground" strokeWidth={2} />
          <span className="font-mono text-[12px] text-foreground">middleware.ts</span>
        </div>
        <div className="flex items-center gap-2 font-mono text-[11px]">
          <span className="text-success">+{adds}</span>
          <span className="text-destructive">-{dels}</span>
        </div>
      </div>
      <div className="py-1 font-mono text-[11px] leading-relaxed">
        {diffLines.map((l, i) => (
          <div key={i} className={cn("flex items-start gap-3 px-3 py-[1px]",
            l.type === "add" && "bg-success/10",
            l.type === "del" && "bg-destructive/10")}>
            <span className={cn("w-3 shrink-0 select-none",
              l.type === "add" && "text-success",
              l.type === "del" && "text-destructive",
              l.type === "ctx" && "text-border")}>
              {l.type === "add" ? "+" : l.type === "del" ? "-" : " "}
            </span>
            <span className={cn("whitespace-pre",
              l.type === "add" && "text-success",
              l.type === "del" && "text-destructive/80",
              l.type === "ctx" && "text-muted-foreground")}>
              {l.text || " "}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function TestResult() {
  return (
    <div className="flex items-center gap-2.5 rounded-md border border-success/20 bg-success/5 px-3 py-2">
      <CheckCircle2 className="size-3.5 shrink-0 text-success" strokeWidth={2.5} />
      <span className="font-mono text-[11px] text-success">auth/edge-session.test.ts</span>
      <span className="text-[11px] text-muted-foreground">(8 tests)</span>
      <span className="ml-auto font-mono text-[11px] text-muted-foreground">412ms</span>
    </div>
  );
}

function UserMessage() {
  return (
    <div className="flex items-start justify-end gap-3">
      <div className="max-w-[70%] rounded-xl bg-[#1a1a1a] px-4 py-3">
        <p className="text-[13px] leading-relaxed text-foreground">
          Refactor auth middleware with Redis rate limiting
        </p>
      </div>
      <div className="flex size-[30px] shrink-0 items-center justify-center rounded-full bg-primary text-[11px] font-bold text-primary-foreground">TJ</div>
    </div>
  );
}

function AgentMessage({ onShip }) {
  return (
    <div className="flex items-start gap-3">
      <div className="flex size-[30px] shrink-0 items-center justify-center rounded-full bg-primary/15 text-[10px] font-bold text-primary ring-1 ring-primary/20">OR</div>
      <div className="min-w-0 flex-1">
        <div className="mb-2 flex items-center gap-2">
          <span className="text-[13px] font-semibold text-foreground">ORA Agent</span>
          <span className="text-[11px] text-muted-foreground">2:15 PM</span>
        </div>
        <div className="rounded-r-xl border-l-2 border-primary bg-[#111111] px-4 py-3 space-y-3">
          <p className="text-[13px] leading-relaxed text-foreground/90">
            I&apos;ve updated <code className="rounded bg-secondary px-1 py-px font-mono text-[11px] text-primary">middleware.ts</code> to use
            Redis-backed rate limiting and switched the runtime to <code className="rounded bg-secondary px-1 py-px font-mono text-[11px] text-primary">edge</code>.
            All existing redirect behavior is preserved.
          </p>
          <DiffBlock />
          <TestResult />
          <div className="flex items-center gap-2 pt-1">
            <button onClick={onShip} data-testid="ds2-open-pr"
              className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-[6px] text-[12px] font-semibold text-primary-foreground transition-opacity hover:opacity-90">
              <GitPullRequest className="size-3.5 shrink-0" strokeWidth={2.5} /> Open pull request
            </button>
            <button className="flex items-center gap-1.5 rounded-md border border-border px-3 py-[6px] text-[12px] font-medium text-muted-foreground transition-colors hover:text-foreground">
              <Check className="size-3.5 shrink-0" strokeWidth={2.5} /> Keep changes
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Composer({ onSend, loopOn, onLoopToggle, onScanTrigger }) {
  const [input, setInput] = useState("");
  const [slashIdx, setSlashIdx] = useState(0);
  const matches = matchSlashCommands(input);
  const menuOpen = matches.length > 0;

  function submitCommand(cmd) {
    // Selecting a slash command fires the scan directly — no LLM
    // round-trip. The strip's `scanState=in_progress` will render
    // from parent state until the promise resolves.
    onScanTrigger?.(cmd);
    setInput("");
    setSlashIdx(0);
  }

  function handleSubmit(e) {
    e.preventDefault();
    if (!input.trim()) return;
    if (menuOpen) {
      // Enter while menu is open = pick highlighted command.
      const cmd = matches[slashIdx] || matches[0];
      submitCommand(cmd);
      return;
    }
    onSend?.(input);
    setInput("");
  }

  function handleKeyDown(e) {
    if (menuOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashIdx((i) => (i + 1) % matches.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashIdx((i) => (i - 1 + matches.length) % matches.length);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setInput("");
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="relative rounded-xl border border-[#222222] bg-[#161616] p-3">
      {menuOpen && (
        <SlashCommandMenu
          matches={matches}
          selectedIndex={slashIdx}
          onPick={(cmd) => submitCommand(cmd)}
        />
      )}
      <textarea value={input} onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        rows={2} placeholder="Ask ORA to build, fix, or scan… (type / for scan commands)"
        data-testid="ds2-composer-input"
        className="w-full resize-none bg-transparent text-[13px] text-foreground placeholder:text-muted-foreground/60 focus:outline-none" />
      <div className="flex items-center gap-2 pt-2">
        <button type="button" className="relative flex items-center justify-center text-muted-foreground transition-colors hover:text-foreground">
          <ShieldAlert className="size-4" strokeWidth={2} />
          <span className="absolute -right-1 -top-1 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-destructive px-[3px] text-[8px] font-bold text-white">3</span>
        </button>
        <button type="button" className="text-muted-foreground transition-colors hover:text-foreground"><BarChart2 className="size-4" strokeWidth={2} /></button>
        <button type="button" className="text-muted-foreground transition-colors hover:text-foreground"><Paperclip className="size-4" strokeWidth={2} /></button>
        <button type="button" className="text-muted-foreground transition-colors hover:text-foreground"><GitBranch className="size-4" strokeWidth={2} /></button>
        <button type="button" onClick={() => onLoopToggle(!loopOn)}
          data-testid="ds2-composer-loop"
          className={cn("flex items-center gap-1.5 rounded-full px-2.5 py-[4px] text-[10px] font-bold uppercase tracking-wide transition-colors",
            loopOn ? "bg-primary text-primary-foreground" : "border border-border text-muted-foreground hover:text-foreground")}>
          <RefreshCw className="size-2.5" strokeWidth={3} /> Loop {loopOn ? "ON" : "OFF"}
        </button>
        <div className="ml-auto">
          <button type="submit" disabled={!input.trim()} aria-label="Send message"
            data-testid="ds2-composer-send"
            className="flex size-8 items-center justify-center rounded-full bg-primary text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-30">
            <ArrowUp className="size-4" strokeWidth={2.5} />
          </button>
        </div>
      </div>
      <p className="mt-2 text-[10px] text-muted-foreground/50">ORA · Vanguard reviews every change before it ships.</p>
    </form>
  );
}

export function ChatView({ onChatStart, onShip, loopOn, onLoopToggle }) {
  const scrollRef = useRef(null);
  const activeProject = useActiveProject();
  const [scanState, setScanState] = useState("idle");   // "idle" | "in_progress"

  function handleSend() {
    onChatStart?.();
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }

  // Iter 212m-190 (Directive Session 3 · Part C) — Slash-command
  // dispatcher. Each command maps to a category slice of the
  // existing `/codebase-health/scan` endpoint (single source of truth).
  // `scanState="in_progress"` triggers the strip's live-progress
  // branch; on completion we stash a session-scoped result via
  // `markScanJustCompleted` so the strip surfaces the critical/high
  // totals (or nothing at all when clean).
  async function runScanCommand(cmd) {
    const projectId = activeProject?.project_id || getActiveProjectId();
    if (!projectId) return;   // never scan a null project
    setScanState("in_progress");
    try {
      const r = await api.post("/codebase-health/scan", {
        project_id: projectId,
        categories: cmd.categories || null,   // null = default full sweep
      });
      const summary = r.data?.summary || {};
      const bySev   = summary.by_severity || {};
      markScanJustCompleted({
        critical:    bySev.critical || 0,
        high:        bySev.high || 0,
        projectId,
        projectName: activeProject?.github_repo || projectId,
      });
    } catch {
      // Network / auth blip — keep the strip silent rather than
      // surfacing a fake-critical or a scary error banner. The user
      // can retry from /codebase-health or the sidebar directly.
    } finally {
      setScanState("idle");
    }
  }

  function openFindingsDrawer() {
    // Session 3 leaves the review-drawer wiring to a follow-up UI
    // task — for now the CTA routes the user to the existing
    // dedicated Codebase Health page which already lists every
    // finding with fix/snooze/dismiss controls.
    window.location.assign("/codebase-health");
  }

  return (
    <div data-testid="ds2-chatview" className="flex h-full flex-col">
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-5 py-6" onScroll={onChatStart}>
        <div className="mx-auto max-w-3xl space-y-6">
          <UserMessage />
          <AgentMessage onShip={onShip} />
        </div>
      </div>
      <div className="shrink-0 space-y-2 px-5 pb-5 pt-2">
        <StreamHealthPill visible />
        <LoopBar />
        <div className="mx-auto max-w-3xl">
          <ScanStatusStrip
            projectId={activeProject?.project_id || getActiveProjectId()}
            scanState={scanState}
            projectName={activeProject?.github_repo || ""}
            onReviewFindings={openFindingsDrawer}
          />
          <Composer
            onSend={handleSend}
            loopOn={loopOn}
            onLoopToggle={onLoopToggle}
            onScanTrigger={runScanCommand}
          />
        </div>
      </div>
    </div>
  );
}
