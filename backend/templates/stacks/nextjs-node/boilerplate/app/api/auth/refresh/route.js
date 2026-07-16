import { NextResponse } from "next/server";
import jwt from "jsonwebtoken";
import { setAuthCookies, clearAuthCookies } from "@/lib/auth";

export async function POST(req) {
  const rt = req.cookies.get("session_r")?.value;
  if (!rt) {
    return NextResponse.json({ error: "No refresh token." }, { status: 401 });
  }
  try {
    const payload = jwt.verify(rt, process.env.JWT_SECRET);
    if (payload.typ !== "refresh") {
      return NextResponse.json({ error: "Wrong token type." }, { status: 401 });
    }
    const res = NextResponse.json({ ok: true });
    setAuthCookies(res, payload.user_id, payload.email);
    return res;
  } catch (e) {
    const res = NextResponse.json(
      { error: "Session expired — please sign in again." },
      { status: 401 },
    );
    clearAuthCookies(res);
    return res;
  }
}
