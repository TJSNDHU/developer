/**
 * DashboardPreviewV2.jsx — Iter 212m-81
 *
 * Preview-only composition page mounted at /dashboard-preview-v2.
 * Mirrors the v0 `app/page.tsx` from `sidebar-changes.zip` — Sidebar
 * (hover-collapsible in Chat, hidden-with-hamburger in Preview/Graph)
 * + TopBar + ChatView/PreviewPanel/GraphView + AskAdvisor side panel
 * + Ship modal. Wrapped in `.ds2-root` so the new Tailwind tokens
 * resolve without affecting the rest of the app.
 *
 * IMPORTANT: nothing here touches the real ChatPanel.jsx, auth
 * flows, or backend APIs. Approve the look, then I wire it in.
 */
import React, { useEffect, useState } from "react";
import { cn } from "../components/dashboard/v2/cn";
import { Sidebar }       from "../components/dashboard/v2/Sidebar";
import { TopBar }        from "../components/dashboard/v2/TopBar";
import { ChatView }      from "../components/dashboard/v2/ChatView";
import { PreviewPanel }  from "../components/dashboard/v2/PreviewPanel";
import { GraphView }     from "../components/dashboard/v2/GraphView";
import { AskAdvisor }    from "../components/dashboard/v2/AskAdvisor";
import { shipFiles } from "../components/dashboard/v2/dashboard-data";
import { X, CheckCircle2, Rocket, Menu } from "lucide-react";

function ShipModal({ onClose }) {
  return (
    <div data-testid="ds2-ship-modal"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="relative w-full max-w-[480px] overflow-hidden rounded-xl border border-border bg-[#161616] shadow-2xl">
        <div className="h-[2px] w-full bg-primary" />
        <div className="flex items-center justify-between px-6 pt-5 pb-4">
          <div className="flex items-center gap-2.5">
            <Rocket className="size-5 text-primary" strokeWidth={2} />
            <h2 className="text-[18px] font-bold tracking-tight text-foreground">Ship via CTO</h2>
          </div>
          <button onClick={onClose} aria-label="Close modal"
            data-testid="ds2-ship-close"
            className="flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground">
            <X className="size-4" strokeWidth={2} />
          </button>
        </div>
        <div className="mx-6 mb-4 overflow-hidden rounded-lg border border-border bg-[#111111]">
          <div className="border-b border-border px-4 py-2.5">
            <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">Files changed</p>
          </div>
          <ul className="divide-y divide-border">
            {shipFiles.map((f) => (
              <li key={f.path} className="flex items-center justify-between px-4 py-3">
                <span className="font-mono text-[12px] text-foreground">{f.path}</span>
                <div className="flex items-center gap-2 font-mono text-[11px]">
                  <span className="text-success">+{f.added}</span>
                  <span className="text-destructive">-{f.removed}</span>
                </div>
              </li>
            ))}
          </ul>
        </div>
        <div className="mx-6 mb-5 flex items-center gap-2.5 rounded-lg border border-success/20 bg-[#0a1a0a] px-4 py-3">
          <CheckCircle2 className="size-4 shrink-0 text-success" strokeWidth={2.5} />
          <p className="text-[13px] font-semibold text-success">Vanguard clean · 0 critical</p>
        </div>
        <div className="flex items-center justify-end gap-2.5 border-t border-border px-6 py-4">
          <button onClick={onClose} className="rounded-md border border-border px-4 py-2 text-[13px] font-medium text-muted-foreground transition-colors hover:border-border/80 hover:text-foreground">
            Cancel
          </button>
          <button onClick={onClose} data-testid="ds2-ship-confirm"
            className="flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-[13px] font-semibold text-primary-foreground transition-opacity hover:opacity-90">
            <Rocket className="size-3.5" strokeWidth={2.5} /> Ship it
          </button>
        </div>
      </div>
    </div>
  );
}

export default function DashboardPreviewV2() {
  const [tab,              setTab]              = useState("Chat");
  const [mode,             setMode]             = useState("maxx");
  const [chatActive,       setChatActive]       = useState(false);
  const [pinned,           setPinned]           = useState(false);
  const [hovered,          setHovered]          = useState(false);
  const [advisorCollapsed, setAdvisorCollapsed] = useState(false);
  const [topbarHidden,     setTopbarHidden]     = useState(false);
  const [modalOpen,        setModalOpen]        = useState(false);
  const [loopOn,           setLoopOn]           = useState(false);
  const [sidebarShownOnFull, setSidebarShownOnFull] = useState(false);

  useEffect(() => {
    function onMouseMove(e) { if (e.clientY <= 20) setTopbarHidden(false); }
    window.addEventListener("mousemove", onMouseMove);
    return () => window.removeEventListener("mousemove", onMouseMove);
  }, []);

  const sidebarCollapsed   = chatActive && !pinned;
  const effectiveCollapsed = sidebarCollapsed && !hovered;
  const isFullTab          = tab === "Preview" || tab === "Graph";

  function handleTabChange(next) {
    setTab(next);
    if (next !== "Chat") { setChatActive(false); setHovered(false); setTopbarHidden(false); }
    if (next === "Preview" || next === "Graph") setSidebarShownOnFull(false);
  }
  function handleChatStart() {
    if (!pinned) setChatActive(true);
    setTopbarHidden(true);
  }

  return (
    <div className="ds2-root" data-testid="ds2-page">
      <div className="flex h-screen w-full overflow-hidden bg-background text-foreground">
        {/* Sidebar */}
        {isFullTab && !sidebarShownOnFull ? (
          <div className="hidden h-full items-start lg:flex">
            <button onClick={() => setSidebarShownOnFull(true)} aria-label="Show sidebar"
              data-testid="ds2-sidebar-show"
              className="mt-[14px] flex size-8 items-center justify-center rounded-r-md border-y border-r border-border bg-card text-muted-foreground transition-colors hover:text-foreground">
              <Menu className="size-4" strokeWidth={2} />
            </button>
          </div>
        ) : (
          <div className={cn("hidden h-full shrink-0 lg:block",
            isFullTab && "absolute left-0 top-0 z-40 h-full shadow-xl")}
            onMouseEnter={() => sidebarCollapsed && setHovered(true)}
            onMouseLeave={() => { setHovered(false); if (isFullTab) setSidebarShownOnFull(false); }}>
            <Sidebar collapsed={effectiveCollapsed} pinned={pinned}
              onPinChange={setPinned} loopOn={loopOn} onLoopToggle={setLoopOn} />
          </div>
        )}

        {/* Main column */}
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar tab={tab} onTabChange={handleTabChange}
            mode={mode} onModeChange={setMode}
            hidden={topbarHidden}
            onNewRun={() => setModalOpen(true)} />
          <main className="min-h-0 flex-1 overflow-hidden">
            {tab === "Chat"    && <ChatView onChatStart={handleChatStart} onShip={() => setModalOpen(true)} loopOn={loopOn} onLoopToggle={setLoopOn} />}
            {tab === "Preview" && <PreviewPanel />}
            {tab === "Graph"   && <GraphView />}
          </main>
        </div>

        {/* Advisor */}
        <AskAdvisor collapsed={advisorCollapsed} onCollapse={setAdvisorCollapsed} />

        {/* Modal */}
        {modalOpen && <ShipModal onClose={() => setModalOpen(false)} />}
      </div>
    </div>
  );
}
