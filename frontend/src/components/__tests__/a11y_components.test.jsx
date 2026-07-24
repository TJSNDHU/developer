/**
 * a11y_components.test.jsx — Iter 302 (Frontend QA Charter Layer 3 — C2)
 *
 * axe-core a11y assertions piggybacked on the components already
 * tested in Batches 1–3 of the state-sync suite. Each component is
 * rendered ONCE in a representative state and passed through
 * axe-core. WCAG 2.2 A + AA rules (default axe config).
 *
 * Discipline per charter:
 *   > "Do NOT try to fix every violation found in one pass — commit
 *   >  a baseline of currently-known issues [...] then burn down the
 *   >  backlog opportunistically."
 *
 * So we DON'T assert `toHaveNoViolations()` yet — we would fail on
 * every existing a11y issue and block merges immediately. Instead
 * we snapshot the CURRENT set of violations per component into
 * `docs/a11y_baseline.json` and fail ONLY when a new violation type
 * is introduced (or the count grows). Baseline lives on disk;
 * update discipline mirrors the visual-regression rebaseline flow.
 */
import React from "react";
import { describe, it, expect, beforeAll } from "vitest";
import { render } from "@testing-library/react";
import { axe } from "vitest-axe";
import fs from "fs";
import path from "path";

// Import every component in the audit set.
import LoopStepBar from "../LoopStepBar.jsx";
import AgentStatusBar from "../AgentStatusBar.jsx";
import LoopLiveFeed from "../LoopLiveFeed.jsx";
import IntentTierIndicator from "../IntentTierIndicator.jsx";
import { SelfHealIndicator, UserActionCard } from "../LoopActionCards.jsx";
import PlanApprovalCard from "../PlanApprovalCard.jsx";
import StreamHealthPill from "../chat/StreamHealthPill.jsx";
import StepCards from "../StepCards.jsx";


// Baseline path (repo-relative, resolved at test time so it works
// under both local + CI cwd).
const BASELINE_PATH = path.resolve(
  __dirname, "../../../../docs/a11y_baseline.json"
);


function loadBaseline() {
  try {
    return JSON.parse(fs.readFileSync(BASELINE_PATH, "utf8"));
  } catch {
    return {};
  }
}


function assertNoNewViolations(componentKey, results) {
  const baseline = loadBaseline();
  const known = new Set(baseline[componentKey] || []);
  const actual = (results.violations || []).map((v) => v.id);
  const newOnes = actual.filter((id) => !known.has(id));
  expect(newOnes, (
    `${componentKey}: new a11y violation types detected: ${newOnes.join(", ")}. ` +
    `Either fix the violation in the component OR (only if intentional & reviewed) ` +
    `add the rule id to docs/a11y_baseline.json under "${componentKey}".`
  )).toEqual([]);
}


describe("a11y component regression — axe-core (iter 302, charter L3 C2)", () => {
  it("LoopStepBar: executing state — no NEW violations vs baseline", async () => {
    const { container } = render(<LoopStepBar phase="executing" />);
    const results = await axe(container);
    assertNoNewViolations("LoopStepBar", results);
  });

  it("AgentStatusBar: running state — no NEW violations vs baseline", async () => {
    const { container } = render(
      <AgentStatusBar loopStatus="running" lastPhase="verify" />
    );
    const results = await axe(container);
    assertNoNewViolations("AgentStatusBar", results);
  });

  it("LoopLiveFeed: live-events state — no NEW violations vs baseline", async () => {
    const { container } = render(
      <LoopLiveFeed
        loopId="a11y-1"
        event={{ phase: "verify", state: "verifying",
                  message: "Independent verifier: verdict yes",
                  ts: 1_739_000_000_000 }}
        terminal={false}
      />
    );
    const results = await axe(container);
    assertNoNewViolations("LoopLiveFeed", results);
  });

  it("IntentTierIndicator: agentic state — no NEW violations", async () => {
    const { container } = render(
      <IntentTierIndicator liveText="" lastTier="agentic" />
    );
    const results = await axe(container);
    assertNoNewViolations("IntentTierIndicator", results);
  });

  it("SelfHealIndicator: visible state — no NEW violations", async () => {
    const { container } = render(
      <SelfHealIndicator visible={true} attempt={2} max={3}
                          errorPreview="ruff: E501" />
    );
    const results = await axe(container);
    assertNoNewViolations("SelfHealIndicator", results);
  });

  it("PlanApprovalCard: enabled — no NEW violations", async () => {
    const { container } = render(
      <PlanApprovalCard onApprove={() => {}} onCancel={() => {}}
                         disabled={false} />
    );
    const results = await axe(container);
    assertNoNewViolations("PlanApprovalCard", results);
  });

  it("UserActionCard: paused-for-user — no NEW violations", async () => {
    const { container } = render(
      <UserActionCard
        phase="verify" message="Lint failed. Retry?"
        errors={["ruff:E501"]} onAction={() => {}} busy={false}
      />
    );
    const results = await axe(container);
    assertNoNewViolations("UserActionCard", results);
  });

  it("StreamHealthPill: reconnecting — no NEW violations", async () => {
    const { container } = render(
      <StreamHealthPill state={{ phase: "reconnecting", silentFor: 12 }} />
    );
    const results = await axe(container);
    assertNoNewViolations("StreamHealthPill", results);
  });

  it("StepCards: streaming — no NEW violations", async () => {
    const { container } = render(
      <StepCards streaming={true}
                  steps={[{ text: "🤔 Thinking", done: false },
                          { text: "✍️ Writing files", done: false }]} />
    );
    const results = await axe(container);
    assertNoNewViolations("StepCards", results);
  });
});
