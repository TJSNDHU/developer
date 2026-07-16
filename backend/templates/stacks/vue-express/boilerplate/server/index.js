/**
 * server/index.js — Minimal Express API with JWT auth + AUREM DB.
 *
 * A user can `docker compose up` this and hit /api/health, /api/auth/signup,
 * /api/auth/login without any additional setup — the shared AUREM DB
 * REST proxy handles data. Extend this file with your app's routes.
 */
const express      = require("express");
const bcrypt       = require("bcryptjs");
const jwt          = require("jsonwebtoken");
const cookieParser = require("cookie-parser");
const cors         = require("cors");
require("dotenv").config();

const { db } = require("./aurem-db");

const app = express();
app.use(cors({ origin: process.env.FRONTEND_URL || "http://localhost:3000", credentials: true }));
app.use(express.json());
app.use(cookieParser());

function sign(user) {
  return jwt.sign({ user_id: user.id, email: user.email },
                  process.env.JWT_SECRET, { expiresIn: "30d" });
}
function cookieOpts() {
  return {
    httpOnly: true, sameSite: "lax", path: "/",
    maxAge: 30 * 24 * 60 * 60 * 1000,
    secure: process.env.NODE_ENV === "production",
  };
}
function currentUser(req) {
  const t = req.cookies?.session;
  if (!t) return null;
  try { return jwt.verify(t, process.env.JWT_SECRET); }
  catch { return null; }
}

app.get("/api/health", (_req, res) => res.json({ ok: true }));

app.post("/api/auth/signup", async (req, res) => {
  try {
    const { email, password, name } = req.body || {};
    if (!email || !password || password.length < 8) {
      return res.status(400).json({ error: "Email + password (≥8 chars) required." });
    }
    if (await db.collection("users").findOne({ email })) {
      return res.status(409).json({ error: "That email is already registered." });
    }
    const password_hash = await bcrypt.hash(password, 10);
    const created = await db.collection("users").insert({ email, name: name || "", password_hash });
    const user = { id: created.id || created.data?.id, email, name: name || "" };
    res.cookie("session", sign(user), cookieOpts()).json({ ok: true, ...user });
  } catch (e) { res.status(500).json({ error: "Signup failed." }); }
});

app.post("/api/auth/login", async (req, res) => {
  try {
    const { email, password } = req.body || {};
    const user = await db.collection("users").findOne({ email });
    if (!user) return res.status(401).json({ error: "Invalid credentials." });
    if (!(await bcrypt.compare(password, user.password_hash || "")))
      return res.status(401).json({ error: "Invalid credentials." });
    res.cookie("session", sign(user), cookieOpts()).json({ ok: true, email: user.email, name: user.name });
  } catch (e) { res.status(500).json({ error: "Login failed." }); }
});

app.post("/api/auth/logout", (_req, res) =>
  res.clearCookie("session").json({ ok: true }));

app.get("/api/auth/me", (req, res) => {
  const u = currentUser(req);
  if (!u) return res.status(401).json({ error: "Not signed in." });
  res.json(u);
});

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => console.log(`API listening on ${PORT}`));
