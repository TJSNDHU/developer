import { NextResponse } from "next/server";
import jwt from "jsonwebtoken";

export async function GET(req) {
  const t = req.cookies.get("session")?.value;
  if (!t) return NextResponse.json({ error: "Not signed in." }, { status: 401 });
  try {
    const payload = jwt.verify(t, process.env.JWT_SECRET);
    if (payload.typ !== "access") {
      return NextResponse.json({ error: "Wrong token type." }, { status: 401 });
    }
    return NextResponse.json({ user_id: payload.user_id, email: payload.email });
  } catch {
    return NextResponse.json(
      { error: "Session expired — please refresh." },
      { status: 401 },
    );
  }
}
