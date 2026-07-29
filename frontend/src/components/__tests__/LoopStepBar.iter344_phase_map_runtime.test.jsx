/**
 * Iter 344 — RUNTIME verification of the LoopStepBar phase map
 * (replaces the source-regex approach in backend
 * test_loop_state_frontend_sync.py which broke on multi-key lines).
 *
 * Backend LoopState values are frozen here from
 * services/loop_engine.py::LoopState — if the backend enum grows,
 * this list must grow with it (the backend twin test guards that).
 */
import { describe, it, expect } from "vitest";
import { __PHASE_TO_STEP } from "../LoopStepBar";

const BACKEND_LOOP_STATES = [
  "idle", "planning", "awaiting_confirmation", "executing",
  "verifying", "scanning", "shipping", "self_healing",
  "paused_for_user", "completed", "failed", "aborted", "expired",
];

describe("Iter 344 — PHASE_TO_STEP covers every backend LoopState (runtime)", () => {
  it("every backend state value is a key in the executed map", () => {
    const missing = BACKEND_LOOP_STATES.filter(
      (s) => !(s in __PHASE_TO_STEP),
    );
    expect(missing).toEqual([]);
  });

  it("every mapping is a step number 0-5", () => {
    for (const [k, v] of Object.entries(__PHASE_TO_STEP)) {
      expect(Number.isInteger(v), `${k} → ${v}`).toBe(true);
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(5);
    }
  });
});
