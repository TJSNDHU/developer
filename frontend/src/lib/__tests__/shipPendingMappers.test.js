// Iter 328 hotfix v3 — mapper unit tests.
//
// These tests use the EXACT raw shape founder verified via
// /loop/active fiber inspection on prod: AUDIT.md with additions:1,
// deletions:1, diff_source:"line", + integrity_verdict:"clean".
// If either mapper drops files_diff or integrity_verdict, these
// tests red-light instantly — regardless of what ChatPanel does
// around the call site.
//
// Gap this closes: ShipPendingCard.test.jsx mounts with hand-built
// payloads (so it always had the fields).  A ChatPanel-level bug
// where the *inbound wire object* → *pending state* mapping drops
// fields is exactly what the ShipPendingCard test could not catch —
// this file closes that gap.

import { describe, it, expect } from "vitest";
import {
  mapShipPendingFromActive,
  mapShipPendingFromAwaitingShipEvent,
} from "../shipPendingMappers.js";


// -------------------- REAL /loop/active shape --------------------
// Verified by founder via prod fiber inspection during the second
// Deploy 2 hotfix eyeball attempt.  Do NOT change these values —
// they are the wire truth this mapper must not drop.
const REAL_ACTIVE_SHIP_PENDING = {
  owner: "TJSNDHU",
  repo: "Aurem",
  branch: "main",
  // Backend persists files as a dict {path: new_content}.  Client
  // side only cares about the keys.
  files: { "AUDIT.md": "# Audit\n\nContent." },
  commit_message: "Add AUDIT.md",
  files_diff: [
    {
      path: "AUDIT.md",
      additions: 1,
      deletions: 1,
      is_new: false,
      delta_bytes: 0,
      diff_source: "line",
    },
  ],
  integrity_verdict: "clean",
};

// -------------------- REAL SSE awaiting_ship shape ---------------
const REAL_SSE_DATA = {
  kind: "awaiting_ship",
  owner: "TJSNDHU",
  repo: "Aurem",
  branch: "main",
  files: ["AUDIT.md"],
  file_count: 1,
  commit_message: "Add AUDIT.md",
  files_diff: [
    {
      path: "AUDIT.md",
      additions: 35, deletions: 34,
      is_new: false, delta_bytes: 18, diff_source: "line",
    },
  ],
  integrity_verdict: "clean",
};


describe("mapShipPendingFromActive — the /loop/active rehydrate path", () => {
  it("preserves files_diff array with real prod shape", () => {
    const p = mapShipPendingFromActive(REAL_ACTIVE_SHIP_PENDING);
    expect(Array.isArray(p.files_diff)).toBe(true);
    expect(p.files_diff.length).toBe(1);
    expect(p.files_diff[0].path).toBe("AUDIT.md");
    expect(p.files_diff[0].additions).toBe(1);
    expect(p.files_diff[0].deletions).toBe(1);
    expect(p.files_diff[0].diff_source).toBe("line");
  });

  it("preserves integrity_verdict='clean'", () => {
    const p = mapShipPendingFromActive(REAL_ACTIVE_SHIP_PENDING);
    expect(p.integrity_verdict).toBe("clean");
  });

  it("still normalises files dict → array of paths", () => {
    const p = mapShipPendingFromActive(REAL_ACTIVE_SHIP_PENDING);
    expect(p.files).toEqual(["AUDIT.md"]);
    expect(p.file_count).toBe(1);
  });

  it("returns null on falsy input (fail-open)", () => {
    expect(mapShipPendingFromActive(null)).toBeNull();
    expect(mapShipPendingFromActive(undefined)).toBeNull();
  });

  it("defaults files_diff to [] and verdict to null when backend omits them (pre-Iter-328 loops)", () => {
    const legacy = { ...REAL_ACTIVE_SHIP_PENDING };
    delete legacy.files_diff;
    delete legacy.integrity_verdict;
    const p = mapShipPendingFromActive(legacy);
    expect(p.files_diff).toEqual([]);
    expect(p.integrity_verdict).toBeNull();
    // Other fields still carry through.
    expect(p.owner).toBe("TJSNDHU");
  });
});


describe("mapShipPendingFromAwaitingShipEvent — the SSE path", () => {
  it("preserves files_diff + integrity_verdict from the SSE frame", () => {
    const p = mapShipPendingFromAwaitingShipEvent(REAL_SSE_DATA, { message: "" });
    expect(p.files_diff.length).toBe(1);
    expect(p.files_diff[0].additions).toBe(35);
    expect(p.files_diff[0].deletions).toBe(34);
    expect(p.integrity_verdict).toBe("clean");
  });

  it("uses SSE event.message if present, falls back if missing", () => {
    const withMsg = mapShipPendingFromAwaitingShipEvent(REAL_SSE_DATA, { message: "Custom" });
    expect(withMsg.message).toBe("Custom");
    const noMsg = mapShipPendingFromAwaitingShipEvent(REAL_SSE_DATA, {});
    expect(noMsg.message).toBe("Ready to ship.");
  });

  it("returns null on falsy input (fail-open)", () => {
    expect(mapShipPendingFromAwaitingShipEvent(null, {})).toBeNull();
  });

  it("legacy SSE payload (no files_diff) still yields a valid object with [] + null", () => {
    const legacy = { ...REAL_SSE_DATA };
    delete legacy.files_diff;
    delete legacy.integrity_verdict;
    const p = mapShipPendingFromAwaitingShipEvent(legacy, {});
    expect(p.files_diff).toEqual([]);
    expect(p.integrity_verdict).toBeNull();
  });
});


// -------------- The regression the founder actually hit --------------
// If either mapper drops the two safety fields, this test explicitly
// red-lights.  This is the ONE assertion that would have caught the
// eyeball fail: run the mapper on the raw wire shape, check the
// output object literally includes both keys.
describe("regression guard — the founder's exact eyeball trace", () => {
  it("mapShipPendingFromActive output KEYS include files_diff + integrity_verdict", () => {
    const p = mapShipPendingFromActive(REAL_ACTIVE_SHIP_PENDING);
    expect(Object.keys(p)).toContain("files_diff");
    expect(Object.keys(p)).toContain("integrity_verdict");
  });

  it("mapShipPendingFromAwaitingShipEvent output KEYS include files_diff + integrity_verdict", () => {
    const p = mapShipPendingFromAwaitingShipEvent(REAL_SSE_DATA, {});
    expect(Object.keys(p)).toContain("files_diff");
    expect(Object.keys(p)).toContain("integrity_verdict");
  });
});
