/**
 * LearningHealthTile.test.jsx — Iter 331 · PRD #3-e.
 * Wire-shape → render integration per the QA hard rule: real
 * /admin/learning-health response shape drives the tile.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn() } }));

import { LearningHealthTile } from "../../pages/Admin.jsx";
import { api } from "../../lib/api";

const WIRE_GREEN = {
  status: "green",
  brain: { count: 3, updated_at: "2026-07-28T05:00:00+00:00",
           age_hours: 2.4, project_id: "proj_abc" },
  patterns: { count: 5, last_seen: "2026-07-28T04:00:00+00:00" },
  council_logs: { count: 120, last_24h: 7 },
  canary: { enabled: true, last_run: { verdict: "pass" } },
  eval_cron_enabled: true,
  learning_disabled_flag: false,
};

beforeEach(() => { vi.clearAllMocks(); });

describe("Iter 331 · LearningHealthTile", () => {
  it("renders GREEN status with brain age + counts from the wire shape", async () => {
    api.get.mockResolvedValue({ data: WIRE_GREEN });
    render(<LearningHealthTile />);
    await waitFor(() => {
      expect(screen.getByTestId("learning-health-tile")).toBeInTheDocument();
    });
    expect(api.get).toHaveBeenCalledWith("/admin/learning-health");
    expect(screen.getByTestId("learning-health-status")).toHaveTextContent(/green/i);
    expect(screen.getByTestId("learning-health-brain"))
      .toHaveTextContent(/brains 3 · last write 2.4h ago \(proj_abc\)/);
    expect(screen.getByTestId("learning-health-patterns")).toHaveTextContent("patterns 5");
    expect(screen.getByTestId("learning-health-council"))
      .toHaveTextContent("council logs 120 (7 in 24h)");
    expect(screen.getByText(/canary · ON/)).toBeInTheDocument();
    expect(screen.getByText(/eval cron · ON/)).toBeInTheDocument();
  });

  it("renders RED when brain stale (>24h) and OFF badges when flags disabled", async () => {
    api.get.mockResolvedValue({ data: {
      ...WIRE_GREEN,
      status: "red",
      brain: { count: 2, updated_at: "2026-07-20T05:00:00+00:00",
               age_hours: 192.0, project_id: "proj_old" },
      canary: { enabled: false, last_run: null },
      eval_cron_enabled: false,
    } });
    render(<LearningHealthTile />);
    await waitFor(() => {
      expect(screen.getByTestId("learning-health-status")).toHaveTextContent(/red/i);
    });
    expect(screen.getByText(/canary · OFF/)).toBeInTheDocument();
    expect(screen.getByText(/eval cron · OFF/)).toBeInTheDocument();
  });

  it("fetch failure → renders nothing (fail-open, no crash)", async () => {
    api.get.mockRejectedValue(new Error("401"));
    const { container } = render(<LearningHealthTile />);
    await waitFor(() => { expect(api.get).toHaveBeenCalled(); });
    expect(container.firstChild).toBeNull();
  });
});
