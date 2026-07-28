/**
 * ShipStreakWidget.test.jsx — Iter 331 · QA Charter gap closure.
 *
 * Locks in:
 *   1. Hidden while count is null/0 (no layout shift on slow loads).
 *   2. Renders pill + count when tasks_shipped > 0.
 *   3. Milestone crossing → ONE toast for the HIGHEST unacknowledged
 *      milestone + every lower milestone marked seen in localStorage.
 *   4. Already-acknowledged milestones never re-toast.
 *   5. `aurem:shipped` event triggers a refetch and count updates.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, waitFor } from "@testing-library/react";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn() } }));
vi.mock("../Toast", () => ({ toast: vi.fn() }));

import ShipStreakWidget from "../ShipStreakWidget.jsx";
import { api } from "../../lib/api";
import { toast } from "../Toast";

function mockShipped(n) {
  api.get.mockResolvedValue({ data: { stats: { tasks_shipped: n } } });
}

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});
afterEach(() => { vi.clearAllMocks(); });


describe("Iter 331 · ShipStreakWidget", () => {
  it("1. renders nothing while count is 0", async () => {
    mockShipped(0);
    const { container } = render(<ShipStreakWidget />);
    await act(async () => {});
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId("ship-streak-widget")).toBeNull();
  });

  it("2. renders pill with count when shipped > 0 (below milestone → no toast)", async () => {
    mockShipped(7);
    render(<ShipStreakWidget />);
    await waitFor(() => {
      expect(screen.getByTestId("ship-streak-widget")).toBeInTheDocument();
    });
    expect(screen.getByTestId("ship-streak-count")).toHaveTextContent("7");
    expect(api.get).toHaveBeenCalledWith("/wrapped/me?period=this_week");
    expect(toast).not.toHaveBeenCalled();
  });

  it("3. milestone crossing → one toast for HIGHEST milestone, lower ones auto-marked", async () => {
    mockShipped(30); // crosses 10 AND 25 — must toast 25 only
    render(<ShipStreakWidget />);
    await waitFor(() => { expect(toast).toHaveBeenCalledTimes(1); });
    expect(toast.mock.calls[0][0].message).toContain("25");
    expect(localStorage.getItem("aurem_streak_toast_10")).toBe("1");
    expect(localStorage.getItem("aurem_streak_toast_25")).toBe("1");
    expect(localStorage.getItem("aurem_streak_toast_50")).toBeNull();
  });

  it("4. already-acknowledged milestone never re-toasts", async () => {
    localStorage.setItem("aurem_streak_toast_10", "1");
    localStorage.setItem("aurem_streak_toast_25", "1");
    mockShipped(30);
    render(<ShipStreakWidget />);
    await waitFor(() => {
      expect(screen.getByTestId("ship-streak-count")).toHaveTextContent("30");
    });
    expect(toast).not.toHaveBeenCalled();
  });

  it("5. aurem:shipped event triggers refetch and count updates", async () => {
    mockShipped(3);
    render(<ShipStreakWidget />);
    await waitFor(() => {
      expect(screen.getByTestId("ship-streak-count")).toHaveTextContent("3");
    });
    mockShipped(4);
    await act(async () => {
      window.dispatchEvent(new CustomEvent("aurem:shipped"));
    });
    await waitFor(() => {
      expect(screen.getByTestId("ship-streak-count")).toHaveTextContent("4");
    });
    expect(api.get).toHaveBeenCalledTimes(2);
  });
});
