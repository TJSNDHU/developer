// frontend/src/lib/shipPendingMappers.js — Iter 328 hotfix v3
//
// Pure mappers from wire shape → the local `shipPending` state that
// <ShipPendingCard/> renders from.  Extracted from ChatPanel.jsx so
// the two ingress paths (/loop/active rehydration AND SSE
// awaiting_ship event) share a single mapping function and can be
// unit-tested at the mapping layer — closing the gap the founder
// found in the eyeball trace (a site-#1 fix silently reverted between
// deploys, prop-drop only caught after prod fiber inspection).
//
// Rule: any field ShipPendingCard reads MUST be listed here.  When
// backend adds a new safety field, add it here + add a mapper test —
// then it can never silently drop between ingress and render.

/**
 * @param {object} sp - active.ship_pending from GET /loop/active.
 * @returns {object|null} shipPending state shape, or null if input
 *   is missing.
 */
export function mapShipPendingFromActive(sp) {
  if (!sp || typeof sp !== "object") return null;
  const filesArr = Array.isArray(sp.files)
    ? sp.files
    : Object.keys(sp.files || {});
  return {
    owner:             sp.owner,
    repo:              sp.repo,
    branch:            sp.branch,
    files:             filesArr,
    file_count:        filesArr.length,
    commit_message:    sp.commit_message,
    files_diff:        Array.isArray(sp.files_diff) ? sp.files_diff : [],
    integrity_verdict: sp.integrity_verdict || null,
    message:           "Loop resumed — ready to ship.",
  };
}

/**
 * @param {object} data - the SSE frame's `data` payload for
 *   `kind: "awaiting_ship"`.
 * @param {object} ev - the SSE frame itself (used only for its
 *   optional `.message` string).
 * @returns {object|null} shipPending state shape, or null if input
 *   is missing.
 */
export function mapShipPendingFromAwaitingShipEvent(data, ev) {
  if (!data || typeof data !== "object") return null;
  const filesArr = Array.isArray(data.files) ? data.files : [];
  return {
    owner:             data.owner,
    repo:              data.repo,
    branch:            data.branch,
    files:             filesArr,
    file_count:        data.file_count || filesArr.length,
    commit_message:    data.commit_message || "",
    files_diff:        Array.isArray(data.files_diff) ? data.files_diff : [],
    integrity_verdict: data.integrity_verdict || null,
    message:           (ev && ev.message) || "Ready to ship.",
  };
}
