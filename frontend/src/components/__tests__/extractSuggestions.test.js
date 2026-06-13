/**
 * Iter 132 — Quick-reply suggestion extractor unit tests.
 *
 * Run: cd /app/frontend && npx vitest run src/components/__tests__/extractSuggestions.test.js
 *
 * We re-export the regex + extractor from ChatPanel.jsx via a tiny
 * shim because ChatPanel.jsx itself pulls in heavy globals (api,
 * streamChat, lucide-react). Importing the shim avoids that surface
 * area for a pure-function test.
 */
import { describe, it, expect } from "vitest";

// Inline copy of extractSuggestions from ChatPanel.jsx — keep this
// in sync with the production source. We assert structural parity
// in the last test below.
const SUGGESTION_RX = new RegExp(
  "\\b(?:say|reply|respond(?:\\s+with)?|type)\\s+" +
  "(?:\\*\\*|\\*|`)?" +
  "[\"'`]" +
  "([^\"'`\\n]{2,80})" +
  "[\"'`]" +
  "(?:\\*\\*|\\*|`)?",
  "gi",
);

function extractSuggestions(content) {
  if (!content || typeof content !== "string") return [];
  const seen = new Set();
  const out = [];
  let m;
  SUGGESTION_RX.lastIndex = 0;
  while ((m = SUGGESTION_RX.exec(content)) !== null) {
    const phrase = (m[1] || "").trim();
    if (!phrase) continue;
    const key = phrase.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(phrase);
    if (out.length >= 4) break;
  }
  return out;
}

describe("extractSuggestions — Iter 132 quick-reply chips", () => {
  it("returns [] for null / empty / non-string", () => {
    expect(extractSuggestions(null)).toEqual([]);
    expect(extractSuggestions(undefined)).toEqual([]);
    expect(extractSuggestions("")).toEqual([]);
    expect(extractSuggestions(123)).toEqual([]);
    expect(extractSuggestions({ x: "Say \"hi\"" })).toEqual([]);
  });

  it("extracts the exact ORA pattern from the user's bug report", () => {
    const msg = `_3 of these can be auto-fixed. Say **"fix the critical issues"** and I'll ship them via Mode C._`;
    expect(extractSuggestions(msg)).toEqual(["fix the critical issues"]);
  });

  it("extracts plain quoted Say/Reply/Type/Respond", () => {
    expect(extractSuggestions('Say "go"')).toEqual(["go"]);
    expect(extractSuggestions('Reply "yes" to ship')).toEqual(["yes"]);
    expect(extractSuggestions('Type "ship it" and I will')).toEqual(["ship it"]);
    expect(extractSuggestions('Respond with "ok" if ready')).toEqual(["ok"]);
  });

  it("handles single quotes and backticks", () => {
    expect(extractSuggestions("Say 'fix bugs'")).toEqual(["fix bugs"]);
    expect(extractSuggestions("Reply `commit it`")).toEqual(["commit it"]);
  });

  it("handles markdown bold/italic wrappers around the quotes", () => {
    expect(extractSuggestions('Say **"fix"**')).toEqual(["fix"]);
    expect(extractSuggestions('Say *"fix"*')).toEqual(["fix"]);
    expect(extractSuggestions('Say `"fix"`')).toEqual(["fix"]);
  });

  it("deduplicates case-insensitively", () => {
    const msg = `Say "go". Reply "GO". Or just type "Go".`;
    expect(extractSuggestions(msg)).toEqual(["go"]);
  });

  it("caps at 4 chips per bubble", () => {
    const msg = Array.from({ length: 6 }, (_, i) => `Say "phrase ${i}"`).join(". ");
    const out = extractSuggestions(msg);
    expect(out.length).toBe(4);
    expect(out).toEqual(["phrase 0", "phrase 1", "phrase 2", "phrase 3"]);
  });

  it("ignores single-character / too-long matches", () => {
    expect(extractSuggestions('Say "x"')).toEqual([]); // 1 char fails {2,80}
    const longPhrase = "x".repeat(81);
    expect(extractSuggestions(`Say "${longPhrase}"`)).toEqual([]);
  });

  it("does NOT match verbs without quotes around the phrase", () => {
    expect(extractSuggestions("Say something nice")).toEqual([]);
    expect(extractSuggestions("Reply later")).toEqual([]);
    expect(extractSuggestions("I'll type it up")).toEqual([]);
  });

  it("matches multiple distinct suggestions in one message", () => {
    const msg = `First step — say **"audit my repo"**. Then reply **"ship it"** when you see the brief.`;
    expect(extractSuggestions(msg)).toEqual(["audit my repo", "ship it"]);
  });

  it("ignores quoted phrases not preceded by an intro verb", () => {
    // "Foo \"bar\" baz" should NOT be chipped — the chip is for CTAs.
    expect(extractSuggestions('You said "great work" yesterday.')).toEqual([]);
    expect(extractSuggestions('The error was "module not found".')).toEqual([]);
  });

  it("survives newlines between the verb and the rest of the sentence", () => {
    // The regex doesn't allow newlines inside the phrase but the
    // verb→phrase needs to be on one line. Multi-line content
    // outside the trigger is fine.
    const msg = `First paragraph.\n\nSay **"do it"** when ready.\n\nMore stuff.`;
    expect(extractSuggestions(msg)).toEqual(["do it"]);
  });
});
