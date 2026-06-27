/**
 * theme.test.js — Iter 212m-52 theme controller guards
 *
 * Vitest unit tests (no real DOM beyond document.documentElement).
 * Locks the API contract so future refactors can't silently break
 * the mode-resolution path that every component depends on.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  getThemeMode, getResolvedTheme, setThemeMode, initTheme,
} from "../src/services/theme";

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});


describe("theme: getThemeMode", () => {
  it("returns 'auto' when nothing is stored (default)", () => {
    expect(getThemeMode()).toBe("auto");
  });

  it("returns the stored value when valid", () => {
    localStorage.setItem("aurem_theme", "light");
    expect(getThemeMode()).toBe("light");
    localStorage.setItem("aurem_theme", "dark");
    expect(getThemeMode()).toBe("dark");
  });

  it("falls back to 'auto' on a garbage stored value", () => {
    localStorage.setItem("aurem_theme", "purple");
    expect(getThemeMode()).toBe("auto");
  });
});


describe("theme: getResolvedTheme", () => {
  it("returns explicit dark when mode is dark", () => {
    expect(getResolvedTheme("dark")).toBe("dark");
  });

  it("returns explicit light when mode is light", () => {
    expect(getResolvedTheme("light")).toBe("light");
  });

  it("returns 'light' on auto when OS prefers light", () => {
    window.matchMedia = vi.fn().mockImplementation((q) => ({
      matches: q.includes("light"),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    expect(getResolvedTheme("auto")).toBe("light");
  });

  it("returns 'dark' on auto when OS prefers dark", () => {
    window.matchMedia = vi.fn().mockImplementation(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    expect(getResolvedTheme("auto")).toBe("dark");
  });
});


describe("theme: setThemeMode + apply", () => {
  it("writes the resolved theme to document.documentElement", () => {
    window.matchMedia = vi.fn(() => ({
      matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }));
    setThemeMode("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    setThemeMode("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("persists the choice in localStorage", () => {
    window.matchMedia = vi.fn(() => ({
      matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }));
    setThemeMode("light");
    expect(localStorage.getItem("aurem_theme")).toBe("light");
  });

  it("clamps invalid modes back to auto + applies resolved", () => {
    window.matchMedia = vi.fn(() => ({
      matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }));
    setThemeMode("turquoise");
    expect(localStorage.getItem("aurem_theme")).toBe("auto");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });
});


describe("theme: initTheme", () => {
  it("applies a sane default on cold start (no localStorage, no matchMedia)", () => {
    // jsdom may not provide matchMedia by default.
    delete window.matchMedia;
    initTheme();
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });
});
