import { NextResponse } from "next/server";
import { db } from "@/lib/aurem-db";
import {
  hashPassword, setAuthCookies, rateLimitCheck, clientIP,
} from "@/lib/auth";

export async function POST(req) {
  const rl = rateLimitCheck("signup", clientIP(req));
  if (!rl.ok) {
    return NextResponse.json(
      { error: "Too many requests. Please wait a moment.", retry_after: rl.retryAfter },
      { status: 429, headers: { "Retry-After": String(rl.retryAfter) } },
    );
  }
  try {
    const { email, password, name } = await req.json();
    if (!email || !password || password.length < 8) {
      return NextResponse.json(
        { error: "Please provide an email and a password of at least 8 characters." },
        { status: 400 },
      );
    }
    const lower = String(email).toLowerCase();
    if (await db.collection("users").findOne({ email: lower })) {
      return NextResponse.json(
        { error: "That email can't be used to sign up." },
        { status: 409 },
      );
    }
    const password_hash = await hashPassword(password);
    const created = await db.collection("users").insert({
      email: lower, name: name || "", password_hash,
    });
    const userId = created.id || created.data?.id;
    const res = NextResponse.json({ ok: true, email: lower });
    setAuthCookies(res, userId, lower);
    return res;
  } catch (e) {
    return NextResponse.json({ error: "Signup failed." }, { status: 500 });
  }
}
