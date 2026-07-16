/**
 * server/index.js — Personal Track Express API.
 * Iter 212m-238 security hardening: httpOnly cookies (already), plus
 * refresh tokens, rate limiting, and enumeration-safe password reset.
 */
const express      = require("express");
const bcrypt       = require("bcryptjs");
const jwt          = require("jsonwebtoken");
const cookieParser = require("cookie-parser");
const cors         = require("cors");
const crypto       = require("crypto");
require("dotenv").config();

const { db } = require("./aurem-db");

const app = express();
app.use(cors({
  origin: process.env.FRONTEND_URL || "http://localhost:3000",
  credentials: true,
}));
app.use(express.json());
app.use(cookieParser());

const JWT_SECRET    = process.env.JWT_SECRET;
if (!JWT_SECRET) throw new Error("JWT_SECRET env var is required");

const ACCESS_TTL_S  = 60 * 60;
const REFRESH_TTL_S = 30 * 86400;
const RESET_TTL_S   = 15 * 60;
const IS_PROD       = process.env.NODE_ENV === "production";

function mint(type, userId, email, ttl) {
  return jwt.sign({ typ: type, user_id: userId, email }, JWT_SECRET, { expiresIn: ttl });
}
function cookieOpts(ttl) {
  return {
    httpOnly: true, sameSite: "lax", path: "/",
    maxAge: ttl * 1000, secure: IS_PROD,
  };
}
function setAuthCookies(res, userId, email) {
  res.cookie("session",   mint("access",  userId, email, ACCESS_TTL_S),  cookieOpts(ACCESS_TTL_S));
  res.cookie("session_r", mint("refresh", userId, email, REFRESH_TTL_S), cookieOpts(REFRESH_TTL_S));
}
function clearAuthCookies(res) {
  res.clearCookie("session"); res.clearCookie("session_r");
}
function currentUser(req) {
  const t = req.cookies?.session;
  if (!t) return null;
  try { const p = jwt.verify(t, JWT_SECRET); return p.typ === "access" ? p : null; }
  catch { return null; }
}

// ── Rate limiter (sliding window, in-memory) ─────────────────────
const WINDOWS = new Map(); const LIMIT = 5; const WINDOW_MS = 900_000;
function rateLimit(bucket) {
  return (req, res, next) => {
    const ip = (req.headers["x-forwarded-for"] || req.ip || "unknown").toString().split(",")[0].trim();
    const key = `${bucket}:${ip}`; const now = Date.now();
    const q = (WINDOWS.get(key) || []).filter((t) => t > now - WINDOW_MS);
    if (q.length >= LIMIT) {
      const retry = Math.ceil((WINDOW_MS - (now - q[0])) / 1000);
      return res.status(429).set("Retry-After", String(retry))
        .json({ error: "Too many requests. Please wait a moment.", retry_after: retry });
    }
    q.push(now); WINDOWS.set(key, q); next();
  };
}

// ── Endpoints ────────────────────────────────────────────────────
app.get("/api/health", (_req, res) => res.json({ ok: true }));

app.post("/api/auth/signup", rateLimit("signup"), async (req, res) => {
  try {
    const { email, password, name } = req.body || {};
    if (!email || !password || password.length < 8) {
      return res.status(400).json({ error: "Email + password (≥8 chars) required." });
    }
    const lower = String(email).toLowerCase();
    if (await db.collection("users").findOne({ email: lower })) {
      return res.status(409).json({ error: "That email can't be used to sign up." });
    }
    const password_hash = await bcrypt.hash(password, 12);
    const created = await db.collection("users").insert({ email: lower, name: name || "", password_hash });
    const userId = created.id || created.data?.id;
    setAuthCookies(res, userId, lower);
    res.json({ ok: true, email: lower });
  } catch (e) { res.status(500).json({ error: "Signup failed." }); }
});

app.post("/api/auth/login", rateLimit("login"), async (req, res) => {
  try {
    const { email, password } = req.body || {};
    const lower = String(email || "").toLowerCase();
    const user = await db.collection("users").findOne({ email: lower });
    const hashed = user?.password_hash || await bcrypt.hash("dummy-timing-guard", 12);
    const ok = await bcrypt.compare(password || "", hashed);
    if (!(user && ok)) return res.status(401).json({ error: "Wrong email or password." });
    setAuthCookies(res, user.id, user.email);
    res.json({ ok: true, email: user.email, name: user.name });
  } catch (e) { res.status(500).json({ error: "Login failed." }); }
});

app.post("/api/auth/refresh", (req, res) => {
  const rt = req.cookies?.session_r;
  if (!rt) return res.status(401).json({ error: "No refresh token." });
  try {
    const p = jwt.verify(rt, JWT_SECRET);
    if (p.typ !== "refresh") return res.status(401).json({ error: "Wrong token type." });
    setAuthCookies(res, p.user_id, p.email);
    res.json({ ok: true });
  } catch (e) {
    clearAuthCookies(res);
    res.status(401).json({ error: "Session expired — please sign in again." });
  }
});

app.post("/api/auth/logout", (_req, res) => { clearAuthCookies(res); res.json({ ok: true }); });

app.get("/api/auth/me", (req, res) => {
  const u = currentUser(req);
  if (!u) return res.status(401).json({ error: "Not signed in." });
  res.json({ user_id: u.user_id, email: u.email });
});

// ── Password reset (enumeration-safe) ────────────────────────────
app.post("/api/auth/password-reset-request", rateLimit("reset_request"), async (req, res) => {
  try {
    const lower = String(req.body?.email || "").toLowerCase();
    if (!lower) return res.status(400).json({ error: "Email required." });
    const user = await db.collection("users").findOne({ email: lower });
    if (user) {
      const token = crypto.randomBytes(48).toString("base64url");
      const now = Math.floor(Date.now() / 1000);
      await db.collection("password_reset_tokens").insert({
        token, user_id: user.id, email: lower,
        expires_at: now + RESET_TTL_S, used: false, created_at: now,
      });
      const frontend = process.env.FRONTEND_URL || "http://localhost:3000";
      const resetLink = `${frontend}/reset-password?token=${token}`;
      let sent = false;
      try {
        const { sendResetEmail } = require("./email");
        sent = await sendResetEmail(lower, resetLink);
      } catch { sent = false; }
      if (!sent) {
        console.log(`[password-reset] ${lower} → ${resetLink}`);
      }
    }
    res.status(202).json({
      ok: true,
      message: "If an account matches that email, we've sent a reset link.",
    });
  } catch (e) { res.status(500).json({ error: "Request failed." }); }
});

app.post("/api/auth/password-reset-confirm", rateLimit("reset_confirm"), async (req, res) => {
  try {
    const { token, new_password } = req.body || {};
    if (!token || !new_password || new_password.length < 8) {
      return res.status(400).json({ error: "Token + password (≥8 chars) required." });
    }
    const now = Math.floor(Date.now() / 1000);
    await db.collection("password_reset_tokens").delete({ expires_at: { $lt: now } });
    const row = await db.collection("password_reset_tokens").findOne({ token, used: false });
    if (!row || row.expires_at < now) {
      return res.status(400).json({ error: "That reset link is invalid or has expired." });
    }
    await db.collection("password_reset_tokens").update({ token }, { used: true, used_at: now });
    await db.collection("users").update(
      { id: row.user_id },
      { password_hash: await bcrypt.hash(new_password, 12), password_changed_at: now },
    );
    res.json({ ok: true, message: "Password updated. You can sign in now." });
  } catch (e) { res.status(500).json({ error: "Reset failed." }); }
});

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => console.log(`API listening on ${PORT}`));
