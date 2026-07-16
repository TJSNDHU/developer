/**
 * server/email.js — Password-reset email helper for the Vue+Express
 * boilerplate. Node's built-in fetch (Node 18+) — no additional deps.
 */
const DEFAULT_FROM = process.env.RESEND_FROM ||
                     "AUREM Personal Track <no-reply@auremcto.com>";

async function sendResetEmail(toEmail, resetLink) {
  const key = (process.env.RESEND_API_KEY || "").trim();
  if (!key || !toEmail || !resetLink) return false;
  const subject = "Your password reset link";
  const text = `You asked to reset your password. Link (15 min): ${resetLink}`;
  const html = `<p>You asked to reset your password.</p>` +
               `<p><a href="${resetLink}">Reset my password</a> (link expires in 15 minutes).</p>`;
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
  } catch { return false; }
}

module.exports = { sendResetEmail };
