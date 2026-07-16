/**
 * lib/auth.js — Auth helpers for Personal Track Next.js apps.
 * Iter 212m-238 security hardening.
 *
 * Design invariants:
 *   1. Access token in httpOnly cookie (`session`) — never localStorage.
 *   2. Refresh token in separate httpOnly cookie (`session_r`).
 *   3. In-memory sliding-window rate limiter (5 req / 15 min / IP)
 *      on all sensitive endpoints. Swap for Redis when horizontally scaled.
 *   4. Constant-time password check via bcrypt.
 *   5. Enumeration-safe messaging on signup/login/reset.
 */
import jwt from "jsonwebtoken";
import bcrypt from "bcryptjs";
import { db } from "./aurem-db.js";

const JWT_SECRET     = process.env.JWT_SECRET;
if (!JWT_SECRET) throw new Error("JWT_SECRET env var is required");

export const ACCESS_TTL_S  = 60 * 60;         // 1 hour
export const REFRESH_TTL_S = 30 * 86400;      // 30 days
export const RESET_TTL_S   = 15 * 60;         // 15 min

export function hashPassword(pw)    { return bcrypt.hash(pw, 12); }
export function verifyPassword(pw, hash) { return bcrypt.compare(pw, hash || ""); }

export function mintToken(type, userId, email, ttl) {
  return jwt.sign({ typ: type, user_id: userId, email }, JWT_SECRET,
                  { expiresIn: ttl });
}

export function cookieOpts(ttl) {
  return {
    httpOnly: true, sameSite: "lax", path: "/",
    maxAge: ttl,
    secure: process.env.NODE_ENV === "production",
  };
}

export function setAuthCookies(res, userId, email) {
  res.cookies.set("session",   mintToken("access",  userId, email, ACCESS_TTL_S),
                  cookieOpts(ACCESS_TTL_S));
  res.cookies.set("session_r", mintToken("refresh", userId, email, REFRESH_TTL_S),
                  cookieOpts(REFRESH_TTL_S));
}

export function clearAuthCookies(res) {
  res.cookies.delete("session");
  res.cookies.delete("session_r");
}

// ── Sliding-window rate limiter ──────────────────────────────────
const WINDOWS = new Map();     // key = `${bucket}:${ip}` → array of timestamps
const LIMIT   = 5;
const WINDOW  = 900_000;       // 15 min

export function rateLimitCheck(bucket, ip) {
  const now = Date.now();
  const key = `${bucket}:${ip || "unknown"}`;
  const q = (WINDOWS.get(key) || []).filter((t) => t > now - WINDOW);
  if (q.length >= LIMIT) {
    const retry = Math.ceil((WINDOW - (now - q[0])) / 1000);
    return { ok: false, retryAfter: Math.max(1, retry) };
  }
  q.push(now);
  WINDOWS.set(key, q);
  return { ok: true };
}

export function clientIP(req) {
  const xff = req.headers.get("x-forwarded-for") || "";
  return xff.split(",")[0].trim() || "unknown";
}
