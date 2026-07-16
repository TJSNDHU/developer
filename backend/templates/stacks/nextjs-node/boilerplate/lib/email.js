/**
 * lib/email.js — Password-reset email helper for Personal Track apps.
 *
 * Same shape as the Python and Vue-Express variants. Wraps Resend so
 * generated apps can call one function.  Fails soft if the key is
 * absent — password reset still works, users just don't get an email
 * until the operator sets RESEND_API_KEY.
 */
const DEFAULT_FROM = process.env.RESEND_FROM ||
                     "AUREM Personal Track <no-reply@auremcto.com>";

export async function sendResetEmail(toEmail, resetLink) {
  const key = (process.env.RESEND_API_KEY || "").trim();
  if (!key || !toEmail || !resetLink) return false;
  const subject = "Your password reset link";
  const text = (
    `You (or someone using your email) asked to reset your password.\n\n` +
    `Click the link below within 15 minutes:\n${resetLink}\n\n` +
    `If you didn't request this, you can ignore this email.\n`
  );
  const html = (
    `<p>You (or someone using your email) asked to reset your password.</p>` +
    `<p>Click the link below within 15 minutes:</p>` +
    `<p><a href="${resetLink}">Reset my password</a></p>` +
    `<p>If you didn't request this, you can ignore this email.</p>`
  );
  try {
    const r = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${key}`,
        "Content-Type":  "application/json",
      },
      body: JSON.stringify({
        from: DEFAULT_FROM, to: [toEmail], subject, text, html,
      }),
    });
    return r.ok;
  } catch {
    return false;
  }
}
