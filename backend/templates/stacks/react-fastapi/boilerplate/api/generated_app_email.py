"""
services/generated_app_email.py — Iter 212m-239

Small helper injected into every generated Personal Track app's
boilerplate. Wraps AUREM's Resend integration so a generated app can
send a password-reset email with ONE line:

    from generated_app_email import send_reset_email
    await send_reset_email("user@x.com", "https://myapp/reset?token=...")

Design invariants:
    * No hardcoded API keys — reads from `RESEND_API_KEY` env only.
    * Fails soft: if the key is missing OR the API call fails, the
      helper returns `False` and the caller silently continues (the
      password reset still WORKS — the token is valid — the user just
      doesn't get an email until the operator sets the key).
    * Zero external state — no queues, no retries. Send-once semantics.
"""
from __future__ import annotations

import os
from typing import Optional

try:
    import httpx  # type: ignore
except ImportError:
    httpx = None  # type: ignore


DEFAULT_FROM = os.environ.get(
    "RESEND_FROM", "AUREM Personal Track <no-reply@auremcto.com>",
)


async def send_reset_email(to_email: str, reset_link: str) -> bool:
    """Send a password-reset email via Resend.  Returns True on 2xx,
    False on any failure (no exceptions bubble up)."""
    key = (os.environ.get("RESEND_API_KEY") or "").strip()
    if not key or httpx is None or not to_email or not reset_link:
        return False
    subject = "Your password reset link"
    text = (
        f"You (or someone using your email) asked to reset your password.\n\n"
        f"Click the link below within 15 minutes:\n{reset_link}\n\n"
        f"If you didn't request this, you can ignore this email.\n"
    )
    html = (
        f"<p>You (or someone using your email) asked to reset your password.</p>"
        f"<p>Click the link below within 15 minutes:</p>"
        f"<p><a href=\"{reset_link}\">Reset my password</a></p>"
        f"<p>If you didn't request this, you can ignore this email.</p>"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            r = await cli.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type":  "application/json"},
                json={"from": DEFAULT_FROM, "to": [to_email],
                      "subject": subject, "text": text, "html": html},
            )
        return 200 <= r.status_code < 300
    except Exception:
        return False


__all__ = ["send_reset_email"]
