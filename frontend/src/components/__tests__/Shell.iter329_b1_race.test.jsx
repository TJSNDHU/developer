/**
 * Shell.iter329_b1_race.test.jsx
 *
 * Iter 329 · Chat-history B1-race fix — defer session mint on fresh
 * tab until useActiveProject's auto-seed resolves.
 *
 * Live symptom (founder-observed): fresh incognito-style tab
 * navigation to /dashboard once showed a completely BLANK welcome
 * screen because Shell's session-key effect resolved to
 * "aurem_session_home" and minted a phantom UUID before
 * useActiveProject's /cto/projects/list auto-seed had time to
 * dispatch aurem:project-changed with the real project id.
 *
 * This test file covers three layers:
 *   1. STATIC — source contains the deferral logic + fallback
 *      timeout constant.
 *   2. PURE HELPER — shouldDeferSessionMint() truth table (only
 *      returns true when pid is null AND projects cache has ≥1
 *      project; every other combination = false → mint now).
 *   3. RUNTIME FALLBACK — simulate an auto-seed that never fires
 *      (activeProjectId stays null forever) and confirm the mint
 *      still eventually happens within the fallback window so the
 *      chat isn't stuck permanently blank.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import { render, act } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname  = dirname(__filename);
const SRC = readFileSync(
  resolve(__dirname, "..", "Shell.jsx"),
  "utf-8",
);

import { shouldDeferSessionMint } from "../Shell.jsx";


describe("Iter 329 · B1-race — static source lock", () => {
  it("Shell.jsx contains the B1-race deferral rationale comment", () => {
    expect(SRC).toContain("Iter 329 · Chat-history B1-race fix");
  });

  it("Shell.jsx wires the AUTO_SEED_FALLBACK_MS timeout as founder-mandated safety net", () => {
    expect(SRC).toContain("AUTO_SEED_FALLBACK_MS");
    // Must be a real timeout, not a magical 0. Any value between
    // 1s and 10s is acceptable per founder's "3-5s" guidance;
    // matching exactly 3000 for our chosen default.
    expect(SRC).toMatch(/AUTO_SEED_FALLBACK_MS\s*=\s*3[_,]?000/);
  });

  it("Shell.jsx guards against permanent-blank by falling back to doAdoptOrMint on timeout", () => {
    // The fallback MUST call the same adopt-or-mint path so worst
    // case matches today's behaviour (transient blank), not a
    // permanent wedge.
    expect(SRC).toMatch(/setTimeout\(\s*\(\)\s*=>\s*\{[\s\S]{0,120}doAdoptOrMint\(\)/);
  });
});


describe("Iter 329 · B1-race — shouldDeferSessionMint pure helper", () => {
  it("returns FALSE when activeProjectId is truthy (defer NOT needed)", () => {
    expect(shouldDeferSessionMint("p_abc", JSON.stringify([{ project_id: "p_abc" }])))
      .toBe(false);
  });

  it("returns FALSE when projects cache is missing", () => {
    expect(shouldDeferSessionMint(null, null)).toBe(false);
  });

  it("returns FALSE when projects cache is empty array", () => {
    expect(shouldDeferSessionMint(null, "[]")).toBe(false);
  });

  it("returns FALSE when projects cache is invalid JSON", () => {
    expect(shouldDeferSessionMint(null, "not-json-{{")).toBe(false);
  });

  it("returns FALSE when projects cache is a non-array", () => {
    expect(shouldDeferSessionMint(null, '{"not": "an array"}'))
      .toBe(false);
  });

  it("returns TRUE ONLY when pid=null AND cache has ≥1 project — the exact deferral condition", () => {
    const cache = JSON.stringify([
      { project_id: "p_x", github_owner: "tj", github_repo: "aurem" },
    ]);
    expect(shouldDeferSessionMint(null, cache)).toBe(true);
  });

  it("returns TRUE with multiple cached projects", () => {
    const cache = JSON.stringify([
      { project_id: "p_1" },
      { project_id: "p_2" },
    ]);
    expect(shouldDeferSessionMint(null, cache)).toBe(true);
  });
});


// ── RUNTIME LIVE-REPRO — full useEffect timing behaviour ──────────
// Simulates the exact fresh-tab race: activeProjectId is null at
// mount, projects cache shows the user has a project, and
// aurem:project-changed either fires within the window (correct
// path) or never fires (fallback path). Uses a stripped test
// harness that inlines the same effect logic Shell uses, so we
// don't have to mount the full router/auth stack.
//
// Test harness mirrors Shell.jsx:98-197 verbatim modulo the
// setSessionIdState → onMint prop and the api dep injection.
function TestSessionEffect({ initialPid, projectsCache, apiGetSessions, onMint }) {
  const [activeProjectId, setActiveProjectId] = React.useState(initialPid);
  React.useEffect(() => {
    const onChange = (e) => setActiveProjectId(e?.detail?.pid || null);
    window.addEventListener("aurem:test-project-changed", onChange);
    return () => window.removeEventListener("aurem:test-project-changed", onChange);
  }, []);

  React.useEffect(() => {
    if (activeProjectId) {
      // Pretend cache exists for the pid so we short-circuit like Shell
      // does when a cached project session exists.
    }
    let cancelled = false;
    let fallbackTimer = null;

    const doAdoptOrMint = async () => {
      let adopted = null;
      try {
        const r = await apiGetSessions({ project_id: activeProjectId || "home" });
        const list = r?.sessions || [];
        if (list.length && list[0]?.session_id) adopted = list[0].session_id;
      } catch { /* fall through */ }
      if (cancelled) return;
      const next = adopted || `minted-${Math.random().toString(36).slice(2, 8)}`;
      onMint(next);
    };

    const shouldDefer = shouldDeferSessionMint(activeProjectId, projectsCache);
    if (shouldDefer) {
      const AUTO_SEED_FALLBACK_MS = 3_000;
      fallbackTimer = setTimeout(() => {
        if (cancelled) return;
        doAdoptOrMint();
      }, AUTO_SEED_FALLBACK_MS);
    } else {
      doAdoptOrMint();
    }
    return () => {
      cancelled = true;
      if (fallbackTimer) clearTimeout(fallbackTimer);
    };
  }, [activeProjectId, projectsCache, apiGetSessions, onMint]);
  return null;
}


describe("Iter 329 · B1-race — runtime deferral + fallback (live-repro)", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(()  => { vi.useRealTimers(); });

  it("FRESH TAB with cached projects: mint is DEFERRED — no premature session id emitted", async () => {
    const onMint = vi.fn();
    const apiGetSessions = vi.fn().mockResolvedValue({ sessions: [] });
    // Cache says user has 1 project — auto-seed will fire soon.
    const cache = JSON.stringify([{ project_id: "p_real", github_owner: "tj", github_repo: "aurem" }]);

    render(
      <TestSessionEffect
        initialPid={null}
        projectsCache={cache}
        apiGetSessions={apiGetSessions}
        onMint={onMint}
      />
    );

    // No mint attempt yet — we deferred.
    expect(onMint).not.toHaveBeenCalled();
    expect(apiGetSessions).not.toHaveBeenCalled();

    // Advance 500ms — still no mint (we're within the 3s window).
    await act(async () => { vi.advanceTimersByTime(500); });
    expect(onMint).not.toHaveBeenCalled();
  });

  it("FRESH TAB → auto-seed dispatches WITHIN window → deferred mint yields to real pid path", async () => {
    const onMint = vi.fn();
    const apiGetSessions = vi.fn().mockResolvedValue({
      sessions: [{ session_id: "server-adopted-abc" }],
    });
    const cache = JSON.stringify([{ project_id: "p_real" }]);

    render(
      <TestSessionEffect
        initialPid={null}
        projectsCache={cache}
        apiGetSessions={apiGetSessions}
        onMint={onMint}
      />
    );
    expect(onMint).not.toHaveBeenCalled();

    // useActiveProject auto-seed lands after 300ms — dispatches
    // aurem:test-project-changed with the pid. Effect re-fires with
    // truthy pid → shouldDefer=false → adopt/mint proceeds
    // immediately with the real project context.
    await act(async () => {
      vi.advanceTimersByTime(300);
      window.dispatchEvent(new CustomEvent("aurem:test-project-changed",
        { detail: { pid: "p_real" } }));
      await Promise.resolve();
      await Promise.resolve();
    });
    // Give the promise chain a beat.
    await act(async () => {
      vi.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(apiGetSessions).toHaveBeenCalledWith({ project_id: "p_real" });
    expect(onMint).toHaveBeenCalledWith("server-adopted-abc");
  });

  it("FRESH TAB → auto-seed HANGS (never dispatches) → fallback timer fires at 3s → mint proceeds anyway", async () => {
    const onMint = vi.fn();
    const apiGetSessions = vi.fn().mockResolvedValue({ sessions: [] });
    const cache = JSON.stringify([{ project_id: "p_real" }]);

    render(
      <TestSessionEffect
        initialPid={null}
        projectsCache={cache}
        apiGetSessions={apiGetSessions}
        onMint={onMint}
      />
    );

    // 2.9s in — still nothing (we're just under the 3s cap).
    await act(async () => { vi.advanceTimersByTime(2_900); });
    expect(onMint).not.toHaveBeenCalled();

    // 3.0s → fallback fires. Founder-mandated safety net: worst-case
    // matches pre-fix behaviour (transient blank flash), never a
    // permanent stuck-blank.
    await act(async () => {
      vi.advanceTimersByTime(200);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(apiGetSessions).toHaveBeenCalled();
    expect(onMint).toHaveBeenCalledTimes(1);
  });

  it("FRESH TAB, no projects cache → NO deferral (nothing to wait for), mint fires immediately", async () => {
    const onMint = vi.fn();
    const apiGetSessions = vi.fn().mockResolvedValue({ sessions: [] });
    render(
      <TestSessionEffect
        initialPid={null}
        projectsCache={null}
        apiGetSessions={apiGetSessions}
        onMint={onMint}
      />
    );
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(apiGetSessions).toHaveBeenCalledWith({ project_id: "home" });
    expect(onMint).toHaveBeenCalled();
  });
});
