/**
 * Enumeration-safe password reset — request + confirm.
 * Same pattern as the react-fastapi boilerplate.
 */
import { NextResponse } from "next/server";
import crypto from "crypto";
import { db } from "@/lib/aurem-db";
import {
  RESET_TTL_S, hashPassword, rateLimitCheck, clientIP,
} from "@/lib/auth";

export async function POST(req) {
  const rl = rateLimitCheck("reset_request", clientIP(req));
  if (!rl.ok) {
    return NextResponse.json(
      { error: "Too many requests.", retry_after: rl.retryAfter },
      { status: 429 },
    );
  }
  try {
    const { email } = await req.json();
    if (!email) {
      return NextResponse.json({ error: "Email required." }, { status: 400 });
    }
    const lower = String(email).toLowerCase();
    const user = await db.collection("users").findOne({ email: lower });
    if (user) {
      const token = crypto.randomBytes(48).toString("base64url");
      await db.collection("password_reset_tokens").insert({
        token, user_id: user.id, email: lower,
        expires_at: Math.floor(Date.now() / 1000) + RESET_TTL_S,
        used: false, created_at: Math.floor(Date.now() / 1000),
      });
      const frontend = process.env.FRONTEND_URL || "http://localhost:3000";
      const resetLink = `${frontend}/reset-password?token=${token}`;
      let sent = false;
      try {
        const { sendResetEmail } = await import("@/lib/email");
        sent = await sendResetEmail(lower, resetLink);
      } catch { sent = false; }
      if (!sent) {
        // eslint-disable-next-line no-console
        console.log(`[password-reset] ${lower} → ${resetLink}`);
      }
    }
    return NextResponse.json({
      ok: true,
      message: "If an account matches that email, we've sent a reset link.",
    }, { status: 202 });
  } catch (e) {
    return NextResponse.json({ error: "Request failed." }, { status: 500 });
  }
}
