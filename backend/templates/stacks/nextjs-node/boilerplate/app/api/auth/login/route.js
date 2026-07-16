import { NextResponse } from "next/server";
import { db } from "@/lib/aurem-db";
import {
  verifyPassword, hashPassword, setAuthCookies, rateLimitCheck, clientIP,
} from "@/lib/auth";

export async function POST(req) {
  const rl = rateLimitCheck("login", clientIP(req));
  if (!rl.ok) {
    return NextResponse.json(
      { error: "Too many requests. Please wait a moment.", retry_after: rl.retryAfter },
      { status: 429, headers: { "Retry-After": String(rl.retryAfter) } },
    );
  }
  try {
    const { email, password } = await req.json();
    if (!email || !password) {
      return NextResponse.json({ error: "Email and password required." }, { status: 400 });
    }
    const lower = String(email).toLowerCase();
    const user = await db.collection("users").findOne({ email: lower });
    // Timing-constant check: always run bcrypt.
    const hashed = user?.password_hash || await hashPassword("dummy-timing-guard");
    const ok = await verifyPassword(password, hashed);
    if (!(user && ok)) {
      return NextResponse.json({ error: "Wrong email or password." }, { status: 401 });
    }
    const res = NextResponse.json({ ok: true, email: user.email, name: user.name });
    setAuthCookies(res, user.id, user.email);
    return res;
  } catch (e) {
    return NextResponse.json({ error: "Login failed." }, { status: 500 });
  }
}
