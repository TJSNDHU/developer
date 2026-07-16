import { NextResponse } from "next/server";
import { db } from "@/lib/aurem-db";
import { hashPassword, rateLimitCheck, clientIP } from "@/lib/auth";

export async function POST(req) {
  const rl = rateLimitCheck("reset_confirm", clientIP(req));
  if (!rl.ok) {
    return NextResponse.json({ error: "Too many requests." }, { status: 429 });
  }
  try {
    const { token, new_password } = await req.json();
    if (!token || !new_password || new_password.length < 8) {
      return NextResponse.json({ error: "Token + password (≥8 chars) required." }, { status: 400 });
    }
    const now = Math.floor(Date.now() / 1000);
    // Best-effort cleanup of expired tokens
    await db.collection("password_reset_tokens").delete({ expires_at: { $lt: now } });
    const row = await db.collection("password_reset_tokens").findOne({ token, used: false });
    if (!row || row.expires_at < now) {
      return NextResponse.json({ error: "That reset link is invalid or has expired." }, { status: 400 });
    }
    await db.collection("password_reset_tokens").update(
      { token }, { used: true, used_at: now },
    );
    await db.collection("users").update(
      { id: row.user_id },
      { password_hash: await hashPassword(new_password), password_changed_at: now },
    );
    return NextResponse.json({ ok: true, message: "Password updated. You can sign in now." });
  } catch (e) {
    return NextResponse.json({ error: "Reset failed." }, { status: 500 });
  }
}
