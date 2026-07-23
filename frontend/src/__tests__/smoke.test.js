/**
 * Iter 289 (Track 1 Lane A) — trivial coverage smoke test.
 *
 * This test is deliberately minimal: its only job is to prove that
 * vitest + @vitest/coverage-v8 are wired end-to-end and can emit a
 * real coverage-summary.json for the QA MCP tool to consume. As
 * more frontend components acquire proper unit tests, extend this
 * file (or add siblings) — but keep at least one assertion so the
 * pipeline breaks loudly if the setup regresses.
 */
import { describe, it, expect } from "vitest";

describe("iter289 vitest smoke", () => {
  it("boots vitest + jsdom", () => {
    expect(typeof window).toBe("object");
    expect(typeof document).toBe("object");
  });
});
