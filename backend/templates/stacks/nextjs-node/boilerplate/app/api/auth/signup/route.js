/**
 * app/api/auth/signup/route.js — JWT signup endpoint.
 *
 * Non-technical author's note: this is how new users register.
 * Passwords are HASHED with bcrypt before storage — we never
 * save the plain text. On success we mint a JWT and set it as
 * an httpOnly cookie the browser sends on every subsequent
 * request.
 */
import { NextResponse } from "next/server";
import bcrypt from "bcryptjs";
import jwt from "jsonwebtoken";
import { db } from "@/lib/aurem-db";

export async function POST(req) {
  try {
    const { email, password, name } = await req.json();
    if (!email || !password || password.length < 8) {
      return NextResponse.json(
        { error: "Please provide an email and a password of at least 8 characters." },
        { status: 400 },
      );
    }
    const existing = await db.collection("users").findOne({ email });
    if (existing) {
      return NextResponse.json(
        { error: "An account with that email already exists." },
        { status: 409 },
      );
    }
    const hashed = await bcrypt.hash(password, 10);
    const created = await db.collection("users").insert({
      email, name: name || "", password_hash: hashed,
    });
    const token = jwt.sign(
      { user_id: created.id || created.data?.id, email },
      process.env.JWT_SECRET,
      { expiresIn: "30d" },
    );
    const res = NextResponse.json({ ok: true, email });
    res.cookies.set("session", token, {
      httpOnly: true, sameSite: "lax", path: "/",
      maxAge: 30 * 24 * 60 * 60,
      secure: process.env.NODE_ENV === "production",
    });
    return res;
  } catch (e) {
    return NextResponse.json({ error: "Signup failed." }, { status: 500 });
  }
}
