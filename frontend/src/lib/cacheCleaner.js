/**
 * lib/cacheCleaner.js — Iter 212m-25
 *
 * UI cache cleaner. Preserves the user's login (auth token + user
 * profile) but wipes everything else the customer interface might
 * have cached: sessionStorage, non-auth localStorage, IndexedDB,
 * Service Worker caches.
 *
 * Used by:
 *   - Logo click in Shell sidebar (then auto-reloads the current page)
 *   - Explicit "🧹 Clear cache" button under the logo
 *
 * Returns a `{cleared: {…}, errors: [...]}` shape so the caller can
 * surface what happened in a toast.
 */

// Keys we MUST preserve so the user isn't logged out.
const AUTH_KEYS = ["aurem_token", "aurem_user"];

export async function clearUICache() {
  const cleared = {
    localStorage:    0,
    sessionStorage:  0,
    indexedDB:       0,
    caches:          0,
  };
  const errors = [];

  // 1. localStorage — wipe everything EXCEPT the auth keys.
  try {
    const keysToRemove = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && !AUTH_KEYS.includes(k)) keysToRemove.push(k);
    }
    for (const k of keysToRemove) localStorage.removeItem(k);
    cleared.localStorage = keysToRemove.length;
  } catch (e) {
    errors.push(`localStorage: ${e.message}`);
  }

  // 2. sessionStorage — wipe everything.
  try {
    const n = sessionStorage.length;
    sessionStorage.clear();
    cleared.sessionStorage = n;
  } catch (e) {
    errors.push(`sessionStorage: ${e.message}`);
  }

  // 3. IndexedDB — delete every database we know about.
  try {
    if (window.indexedDB && typeof window.indexedDB.databases === "function") {
      const dbs = await window.indexedDB.databases();
      for (const db of dbs || []) {
        if (!db?.name) continue;
        await new Promise((resolve) => {
          const req = window.indexedDB.deleteDatabase(db.name);
          req.onsuccess = req.onerror = req.onblocked = resolve;
        });
        cleared.indexedDB += 1;
      }
    }
  } catch (e) {
    errors.push(`indexedDB: ${e.message}`);
  }

  // 4. Service Worker caches (CacheStorage API).
  try {
    if (typeof caches !== "undefined" && caches.keys) {
      const names = await caches.keys();
      for (const name of names) {
        await caches.delete(name);
        cleared.caches += 1;
      }
    }
  } catch (e) {
    errors.push(`caches: ${e.message}`);
  }

  return { cleared, errors };
}

/**
 * Clear cache then reload the CURRENT page.
 * Adds a small delay so the toast (if any) is visible to the user.
 */
export async function clearUICacheAndReload(delayMs = 450) {
  const result = await clearUICache();
  // Cache-bust query param + reload so the browser re-fetches HTML
  // without any 304 / 200 (from cache) shortcuts.
  setTimeout(() => {
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("_cc", Date.now().toString(36));
      window.location.replace(url.toString());
    } catch {
      window.location.reload();
    }
  }, delayMs);
  return result;
}
