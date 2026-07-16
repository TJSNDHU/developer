/**
 * main.js — All frontend logic for the plain-HTML starter.
 *
 * Talks to AUREM's managed REST proxy directly from the browser.
 * The AUREM_APP_TOKEN is a public-safe app token (scoped to
 * signup/login endpoints) auto-injected at materialize time. A JWT
 * cookie is set by the proxy on successful auth.
 */
const AUREM_API_BASE = window.AUREM_API_BASE || "https://api.auremcto.com";
const AUREM_APP_ID   = window.AUREM_APP_ID   || "REPLACE_AT_MATERIALIZE";

let mode = "login";

const $ = (id) => document.getElementById(id);

async function api(path, body) {
  const r = await fetch(`${AUREM_API_BASE}/api/managed-db-auth/${AUREM_APP_ID}/${path}`, {
    method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || "Request failed");
  return j;
}

async function checkSession() {
  try {
    const me = await api("me", {});
    if (me?.email) show(me);
  } catch { /* not signed in */ }
}

function show(me) {
  $("auth-view").hidden = true;
  $("app-view").hidden = false;
  $("me-name").textContent = me.name || me.email;
}

$("auth-toggle").addEventListener("click", (e) => {
  e.preventDefault();
  mode = mode === "login" ? "signup" : "login";
  $("auth-title").textContent   = mode === "login" ? "Sign in" : "Create account";
  $("auth-submit").textContent  = mode === "login" ? "Sign in" : "Sign up";
  $("auth-toggle").textContent  = mode === "login"
    ? "Don't have an account? Sign up" : "Already have one? Sign in";
});

$("auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("auth-error");
  err.hidden = true;
  try {
    const j = await api(mode, {
      email:    $("email").value,
      password: $("password").value,
    });
    show(j);
  } catch (ex) {
    err.textContent = ex.message; err.hidden = false;
  }
});

$("signout").addEventListener("click", async () => {
  await api("logout", {}); location.reload();
});

checkSession();
