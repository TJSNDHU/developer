"""
services/mock_reality_check.py — Iter 289 (Track 1 Lane A, Task 2)

Lightweight sanity check: every 1-2 weeks (or on each new deploy),
hit the REAL third-party API endpoints we mock most often and compare
the JSON key-set of the response to what our mocks assume. Not full
Pact-style contract testing — just a "did the shape change?" alarm.

Two upstreams covered:
  - GitHub REST API — GET /repos/{owner}/{repo} (public repo, no auth).
    Mocked heavily in the CTO project connect + scope-drift flows.
  - OpenRouter — GET /api/v1/models (public list endpoint, no auth
    required for shape). Mocked in loop_execute.py + council_router.py.

Public surface:
    check_github()      → dict
    check_openrouter()  → dict
    run_all(timeout=8)  → dict {ok, results, drift_summary}

Never raises for a network hiccup — every failure is captured as a
structured `reason` so the caller sees "ok:false + reason='timeout'"
rather than an exception. This is a diagnostic tool, not a health
gate.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

# Keys our code / mocks assume are present. Sourced from actual usage
# sites (grep the codebase for the field name to prove each entry).
# If the upstream ever drops or renames one of these, `run_all()`
# will surface the drift immediately.
_GITHUB_REPO_KEYS = {
    "id", "name", "full_name", "private", "owner", "html_url",
    "description", "fork", "default_branch",
}
_OPENROUTER_MODEL_KEYS = {
    "id", "name", "created", "context_length", "pricing",
}

_GITHUB_PROBE_URL     = "https://api.github.com/repos/octocat/hello-world"
_OPENROUTER_PROBE_URL = "https://openrouter.ai/api/v1/models"


def _diff_shape(expected: set[str], actual_top: dict) -> dict:
    """Compute the missing + unexpected key deltas at the top level.
    Returns a dict shaped for structured logging.

    Only `missing` (an expected key vanished) is a BREAKING drift that
    would silently break our mocks. `unexpected` (upstream added a new
    field) is INFO-only — mocks continue to work; we surface it so the
    founder can decide whether to widen the expected set. `ok` is
    therefore keyed on missing alone."""
    actual_keys = set(actual_top.keys())
    missing = sorted(expected - actual_keys)
    unexpected = sorted(actual_keys - expected)
    return {
        "expected":         sorted(expected),
        "present":          sorted(actual_keys & expected),
        "missing":          missing,
        "unexpected":       unexpected,
        "breaking_drift":   bool(missing),
        "info_drift_only":  bool(unexpected and not missing),
    }


async def check_github(timeout: float = 8.0) -> dict:
    """Hit GitHub's REST API for a public repo and compare shape."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            resp = await c.get(_GITHUB_PROBE_URL,
                                headers={"Accept": "application/vnd.github+json"})
    except httpx.TimeoutException:
        return {"ok": False, "upstream": "github", "reason": "timeout",
                "url": _GITHUB_PROBE_URL}
    except Exception as e:                                       # noqa: BLE001
        return {"ok": False, "upstream": "github",
                "reason": "network_error", "error": repr(e)[:200],
                "url": _GITHUB_PROBE_URL}
    if resp.status_code != 200:
        return {"ok": False, "upstream": "github",
                "reason": "unexpected_status", "status": resp.status_code,
                "url": _GITHUB_PROBE_URL}
    try:
        body = resp.json()
    except json.JSONDecodeError:
        return {"ok": False, "upstream": "github",
                "reason": "invalid_json", "url": _GITHUB_PROBE_URL}
    if not isinstance(body, dict):
        return {"ok": False, "upstream": "github",
                "reason": "not_a_dict", "url": _GITHUB_PROBE_URL}
    shape = _diff_shape(_GITHUB_REPO_KEYS, body)
    return {
        "ok":      not shape["breaking_drift"],
        "upstream": "github",
        "status":   resp.status_code,
        "shape":    shape,
        "url":      _GITHUB_PROBE_URL,
    }


async def check_openrouter(timeout: float = 8.0) -> dict:
    """Hit OpenRouter's public model list and compare shape of the
    first model entry."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            resp = await c.get(_OPENROUTER_PROBE_URL)
    except httpx.TimeoutException:
        return {"ok": False, "upstream": "openrouter",
                "reason": "timeout", "url": _OPENROUTER_PROBE_URL}
    except Exception as e:                                       # noqa: BLE001
        return {"ok": False, "upstream": "openrouter",
                "reason": "network_error", "error": repr(e)[:200],
                "url": _OPENROUTER_PROBE_URL}
    if resp.status_code != 200:
        return {"ok": False, "upstream": "openrouter",
                "reason": "unexpected_status", "status": resp.status_code,
                "url": _OPENROUTER_PROBE_URL}
    try:
        body = resp.json()
    except json.JSONDecodeError:
        return {"ok": False, "upstream": "openrouter",
                "reason": "invalid_json", "url": _OPENROUTER_PROBE_URL}
    data = (body or {}).get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or not data:
        return {"ok": False, "upstream": "openrouter",
                "reason": "empty_or_wrong_top_shape",
                "top_keys": sorted(body.keys()) if isinstance(body, dict) else [],
                "url": _OPENROUTER_PROBE_URL}
    first = data[0] if isinstance(data[0], dict) else {}
    shape = _diff_shape(_OPENROUTER_MODEL_KEYS, first)
    return {
        "ok":       not shape["breaking_drift"],
        "upstream": "openrouter",
        "status":   resp.status_code,
        "shape":    shape,
        "model_count": len(data),
        "sample_id":   first.get("id"),
        "url":         _OPENROUTER_PROBE_URL,
    }


async def run_all(timeout: float = 8.0) -> dict:
    """Fire both probes in parallel; aggregate the drift signal.

    Returns:
      {
        "ok":              True | False,  # False if ANY probe drifted
        "results":         [dict, dict],   # per-upstream reports
        "drift_summary":   [{"upstream": ..., "missing":[...], "unexpected":[...]}]
      }
    """
    import asyncio
    results: list[Any] = await asyncio.gather(
        check_github(timeout=timeout),
        check_openrouter(timeout=timeout),
        return_exceptions=True,
    )
    normalised: list[dict] = []
    drifts:     list[dict] = []
    for r in results:
        if isinstance(r, Exception):
            normalised.append({
                "ok": False, "upstream": "unknown",
                "reason": "unhandled_exception",
                "error": repr(r)[:200],
            })
            continue
        normalised.append(r)
        shape = (r.get("shape") or {}) if isinstance(r, dict) else {}
        if shape.get("breaking_drift"):
            drifts.append({
                "upstream":   r.get("upstream"),
                "missing":    shape.get("missing") or [],
                "kind":       "breaking",
            })
        elif shape.get("info_drift_only"):
            drifts.append({
                "upstream":   r.get("upstream"),
                "unexpected": shape.get("unexpected") or [],
                "kind":       "info_only",
            })
    return {
        "ok":            all(r.get("ok") for r in normalised),
        "results":       normalised,
        "drift_summary": drifts,
    }
