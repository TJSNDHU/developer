/**
 * GraphView.jsx — Iter 212m-81 — JSX port of v0 `graph-view.tsx`.
 */
import React, { useState } from "react";
import { cn } from "./cn";
import { graphNodes, graphEdges } from "./dashboard-data";
import { FileCode2, Maximize2, Minus, Plus } from "lucide-react";

const kindStyles = {
  entry:  { ring: "border-border",  dot: "bg-muted-foreground", label: "Entry" },
  module: { ring: "border-border",  dot: "bg-chart-4",          label: "Module" },
  lib:    { ring: "border-border",  dot: "bg-muted-foreground", label: "Library" },
  active: { ring: "border-primary", dot: "bg-primary",          label: "Modified" },
};

export function GraphView() {
  const [selected, setSelected] = useState("mw");
  const nodeById = Object.fromEntries(graphNodes.map((n) => [n.id, n]));

  return (
    <div data-testid="ds2-graph" className="relative h-full overflow-hidden p-4 md:p-6">
      <div className="relative h-full overflow-hidden rounded-xl border border-border bg-card">
        <div className="absolute inset-0 opacity-[0.4]" style={{
          backgroundImage: "radial-gradient(#2A2A2A 1px, transparent 1px)",
          backgroundSize: "22px 22px",
        }} />
        <div className="absolute left-4 top-4 z-20">
          <h2 className="text-sm font-semibold tracking-tight">Dependency graph</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">core-api · 8 modules · feat/edge-auth</p>
        </div>
        <div className="absolute right-4 top-4 z-20 flex flex-col gap-1.5 rounded-lg border border-border bg-background/80 p-2.5 backdrop-blur-sm">
          {["active", "module", "lib"].map((k) => (
            <div key={k} className="flex items-center gap-2 text-[11px] text-muted-foreground">
              <span className={cn("size-2 rounded-full", kindStyles[k].dot)} />
              {kindStyles[k].label}
            </div>
          ))}
        </div>
        <div className="absolute bottom-4 right-4 z-20 flex flex-col overflow-hidden rounded-lg border border-border bg-background/80 backdrop-blur-sm">
          {[Plus, Minus, Maximize2].map((Icon, i) => (
            <button key={i} className={cn("flex size-8 items-center justify-center text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground",
              i < 2 && "border-b border-border")}>
              <Icon className="size-3.5" />
            </button>
          ))}
        </div>
        <svg className="absolute inset-0 h-full w-full" aria-hidden="true">
          {graphEdges.map((e, i) => {
            const a = nodeById[e.from]; const b = nodeById[e.to];
            if (!a || !b) return null;
            const isActive = a.kind === "active" && b.kind === "active";
            return (
              <line key={i} x1={`${a.x}%`} y1={`${a.y}%`} x2={`${b.x}%`} y2={`${b.y}%`}
                stroke={isActive ? "#E8A020" : "#222222"}
                strokeWidth={isActive ? 2 : 1.5}
                strokeOpacity={isActive ? 0.9 : 0.7} />
            );
          })}
        </svg>
        {graphNodes.map((n) => {
          const s = kindStyles[n.kind];
          const isSelected = selected === n.id;
          return (
            <button key={n.id} onClick={() => setSelected(n.id)}
              style={{ left: `${n.x}%`, top: `${n.y}%` }}
              className={cn("absolute z-10 flex -translate-x-1/2 -translate-y-1/2 items-center gap-2 rounded-lg border bg-card px-3 py-2 shadow-lg transition-all hover:scale-[1.04]",
                s.ring, isSelected && "ring-2 ring-primary ring-offset-2 ring-offset-card")}>
              <FileCode2 className={cn("size-3.5", n.kind === "active" ? "text-primary" : "text-muted-foreground")} />
              <span className="font-mono text-xs text-foreground">{n.label}</span>
              <span className={cn("size-1.5 rounded-full", s.dot)} />
            </button>
          );
        })}
      </div>
    </div>
  );
}
