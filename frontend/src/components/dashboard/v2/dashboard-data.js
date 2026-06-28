/**
 * dashboard-data.js — Iter 212m-81 — JS port of `lib/dashboard-data.ts`
 * from the v0 design pack `sidebar-changes.zip`. Sample data only — the
 * real production wiring replaces these arrays with live API responses.
 */

export const repositories = [
  { id: "aurem", owner: "TJSNDHU", name: "Aurem",          branch: "main",        dot: "orange", active: true },
  { id: "atlas", owner: "",        name: "atlas-dashboard", branch: "feat/api",    dot: "gray" },
  { id: "orbit", owner: "",        name: "orbit-payments",  branch: "fix/webhook", dot: "red" },
  { id: "sdk",   owner: "",        name: "sdk-js",          branch: "docs",        dot: "gray" },
];

export const tools = [
  { id: "vanguard", label: "Vanguard Security", badge: "3" },
  { id: "health",   label: "Health Scanner",    score: 87 },
  { id: "loop",     label: "Loop Mode",         toggle: true },
  { id: "graph",    label: "Codebase Graph" },
  { id: "bughunt",  label: "Bug Hunt" },
];

export const diffLines = [
  { type: "ctx", text: "  import { NextResponse } from 'next/server'" },
  { type: "del", text: "- import { getSession } from './lib/auth'" },
  { type: "add", text: "+ import { getCachedSession } from './lib/auth'" },
  { type: "ctx", text: "" },
  { type: "add", text: "+ export const runtime = 'edge'" },
  { type: "ctx", text: "" },
  { type: "ctx", text: "  export async function middleware(req: Request) {" },
  { type: "del", text: "-   const session = await getSession(req)" },
  { type: "add", text: "+   const session = await getCachedSession(req)" },
  { type: "ctx", text: "    if (!session) return NextResponse.redirect('/login')" },
  { type: "ctx", text: "  }" },
];

export const shipFiles = [
  { path: "backend/auth_middleware.py", added: 47, removed: 12 },
  { path: "frontend/ChatPanel.jsx",     added: 8,  removed: 3  },
];

export const previewMeta = { url: "aurem.vercel.app", buildTime: "1.2s" };

export const graphNodes = [
  { id: "mw",      label: "middleware.ts", kind: "active", x: 50, y: 20 },
  { id: "auth",    label: "auth.ts",       kind: "active", x: 30, y: 42 },
  { id: "redis",   label: "redis.ts",      kind: "active", x: 68, y: 42 },
  { id: "session", label: "session.ts",    kind: "module", x: 22, y: 64 },
  { id: "jwt",     label: "jwt.ts",        kind: "lib",    x: 46, y: 64 },
  { id: "db",      label: "db.ts",         kind: "module", x: 74, y: 64 },
  { id: "utils",   label: "utils.ts",      kind: "lib",    x: 18, y: 82 },
  { id: "types",   label: "types.ts",      kind: "lib",    x: 55, y: 82 },
];

export const graphEdges = [
  { from: "mw",      to: "auth" },
  { from: "mw",      to: "redis" },
  { from: "auth",    to: "session" },
  { from: "auth",    to: "jwt" },
  { from: "redis",   to: "db" },
  { from: "session", to: "utils" },
  { from: "jwt",     to: "types" },
  { from: "db",      to: "types" },
];
