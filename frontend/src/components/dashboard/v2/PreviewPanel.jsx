/**
 * PreviewPanel.jsx — Iter 212m-81 — JSX port of v0 `preview-view.tsx`.
 */
import React, { useState } from "react";
import { cn } from "./cn";
import { previewMeta } from "./dashboard-data";
import { ExternalLink, Lock, Monitor, RotateCw, Smartphone, Tablet } from "lucide-react";

const viewports = [
  { id: "desktop", icon: Monitor,    width: "100%" },
  { id: "tablet",  icon: Tablet,     width: "768px" },
  { id: "mobile",  icon: Smartphone, width: "390px" },
];

function PreviewApp() {
  return (
    <div className="min-h-full bg-background text-foreground">
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <div className="flex items-center gap-2">
          <div className="flex size-6 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">A</div>
          <span className="text-sm font-semibold">Aurem Store</span>
        </div>
        <nav className="hidden items-center gap-5 text-xs text-muted-foreground sm:flex">
          <span className="text-foreground">Shop</span><span>Pricing</span><span>Docs</span>
        </nav>
        <button className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground">Sign in</button>
      </header>
      <section className="px-6 py-10 text-center">
        <span className="inline-block rounded-full border border-border bg-card px-2.5 py-1 text-[11px] text-muted-foreground">Now with edge auth</span>
        <h1 className="mx-auto mt-4 max-w-md text-2xl font-semibold tracking-tight">Ship products at the speed of thought</h1>
        <p className="mx-auto mt-2 max-w-sm text-sm text-muted-foreground">A faster checkout, now protected by cached edge sessions.</p>
        <div className="mt-5 flex items-center justify-center gap-2">
          <button className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">Get started</button>
          <button className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-muted-foreground">View demo</button>
        </div>
      </section>
      <section className="grid grid-cols-1 gap-3 px-6 pb-10 sm:grid-cols-3">
        {["Edge sessions", "Optimistic cart", "Instant search"].map((f) => (
          <div key={f} className="rounded-xl border border-border bg-card p-4">
            <div className="mb-3 size-8 rounded-lg bg-primary/15" />
            <p className="text-sm font-medium">{f}</p>
            <p className="mt-1 text-xs text-muted-foreground">Built and verified by the ORA agent.</p>
          </div>
        ))}
      </section>
    </div>
  );
}

export function PreviewPanel() {
  const [viewport, setViewport] = useState("desktop");
  const [reloading, setReloading] = useState(false);
  const active = viewports.find((v) => v.id === viewport);

  function reload() {
    setReloading(true);
    setTimeout(() => setReloading(false), 600);
  }

  return (
    <div data-testid="ds2-preview" className="flex h-full flex-col p-4 md:p-6">
      <div className="flex flex-col overflow-hidden rounded-xl border border-border bg-card shadow-2xl">
        <div className="flex items-center gap-3 border-b border-border px-3 py-2.5">
          <div className="flex items-center gap-1.5">
            <span className="size-3 rounded-full bg-destructive/70" />
            <span className="size-3 rounded-full bg-warning/70" />
            <span className="size-3 rounded-full bg-success/70" />
          </div>
          <button onClick={reload} className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground" aria-label="Reload preview">
            <RotateCw className={cn("size-3.5", reloading && "animate-spin")} />
          </button>
          <div className="flex flex-1 items-center gap-2 rounded-md border border-border bg-background px-3 py-1.5 text-xs text-muted-foreground">
            <Lock className="size-3 text-success" />
            <span className="font-mono text-foreground">{previewMeta.url}</span>
            <span className="ml-auto flex items-center gap-1.5">
              <span className="size-1.5 rounded-full bg-success" />
              <span className="hidden sm:inline">Ready · {previewMeta.buildTime}</span>
            </span>
          </div>
          <div className="hidden items-center gap-0.5 rounded-lg border border-border bg-secondary/50 p-0.5 sm:flex">
            {viewports.map((v) => (
              <button key={v.id} onClick={() => setViewport(v.id)}
                className={cn("flex size-7 items-center justify-center rounded-md transition-colors",
                  viewport === v.id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}
                aria-label={v.id}>
                <v.icon className="size-3.5" />
              </button>
            ))}
          </div>
          <button className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground" aria-label="Open in new tab">
            <ExternalLink className="size-3.5" />
          </button>
        </div>
        <div className="flex flex-1 justify-center overflow-auto bg-[#0b0b14] p-4">
          <div className="h-full overflow-hidden rounded-lg border border-border bg-background transition-all duration-300"
            style={{ width: active.width, maxWidth: "100%" }}>
            <PreviewApp />
          </div>
        </div>
      </div>
    </div>
  );
}
