/**
 * utils/chatBgTint.js — Founder welcome chat-background tint.
 *
 * For the user's first 3 days after signup, the chat panel gets a
 * subtle amber wash that intensifies each day.  Day 4+ returns
 * `"transparent"` so the tint disappears on its own — no DB write
 * required.
 */

/**
 * @param {string | number | Date | null | undefined} createdAt
 *   ISO string, epoch ms, or Date.  Anything unparseable → "transparent".
 * @returns {string} a CSS color value safe to drop into `background`.
 */
export function getChatBgTint(createdAt) {
  if (!createdAt) return "transparent";
  let ts = null;
  if (createdAt instanceof Date) {
    ts = createdAt.getTime();
  } else if (typeof createdAt === "number") {
    // Some legacy rows store unix seconds (< 10^12); newer rows / ISO
    // serialisers use milliseconds.  Auto-promote seconds → ms so the
    // math below stays in one branch.
    ts = createdAt < 1e12 ? createdAt * 1000 : createdAt;
  } else if (typeof createdAt === "string") {
    const parsed = Date.parse(createdAt);
    if (Number.isNaN(parsed)) return "transparent";
    ts = parsed;
  } else {
    return "transparent";
  }
  const hours = (Date.now() - ts) / (1000 * 60 * 60);
  if (hours < 0) return "transparent";  // future-dated → bail
  if (hours < 24) return "rgba(234,179,8,0.04)";
  if (hours < 48) return "rgba(234,179,8,0.07)";
  if (hours < 72) return "rgba(234,179,8,0.11)";
  return "transparent";
}
