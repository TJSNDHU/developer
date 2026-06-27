/**
 * securityScanCache.js — Iter 212m-56
 *
 * Tiny shared store for the latest security-scan summary per
 * project, so the ChatPanel Shield button can render a critical-
 * count badge without re-running the scan itself.
 *
 * Backed by an in-memory Map + an EventTarget. The drawer writes
 * here on every successful scan, and any subscriber (e.g. the
 * Shield button) re-renders via the 'updated' event.
 *
 * Not persisted across reloads — the badge is "live", not historic.
 * If we ever want sticky badges, swap the Map for localStorage.
 */

const _store = new Map();   // projectId → { at, data }
const _bus = new EventTarget();

const TTL_MS = 5 * 60 * 1000;

/**
 * @param {string} projectId
 * @returns {{ at:number, data:object } | null} fresh entry or null
 */
export function getCachedScan(projectId) {
  if (!projectId) return null;
  const hit = _store.get(projectId);
  if (!hit) return null;
  if (Date.now() - hit.at > TTL_MS) return null;
  return hit;
}

/**
 * @param {string} projectId
 * @param {object} data — full /security-scan/run response payload
 */
export function setCachedScan(projectId, data) {
  if (!projectId) return;
  _store.set(projectId, { at: Date.now(), data });
  _bus.dispatchEvent(new CustomEvent("updated", { detail: { projectId } }));
}

/**
 * Subscribe to cache updates. Returns an unsubscribe fn.
 * @param {(projectId:string) => void} fn
 */
export function onScanUpdated(fn) {
  const handler = (e) => fn(e?.detail?.projectId);
  _bus.addEventListener("updated", handler);
  return () => _bus.removeEventListener("updated", handler);
}

/**
 * Convenience getter for the summary tile counts.
 * @param {string} projectId
 * @returns {{ critical:number, high:number, medium:number, low:number, total:number } | null}
 */
export function getScanSeverityCounts(projectId) {
  const hit = getCachedScan(projectId);
  if (!hit) return null;
  const s = hit.data?.summary || {};
  return {
    critical: s.by_severity?.critical || 0,
    high:     s.by_severity?.high     || 0,
    medium:   s.by_severity?.medium   || 0,
    low:      s.by_severity?.low      || 0,
    total:    s.total                 || 0,
  };
}
