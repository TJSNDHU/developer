"""
test_iter313_version_sha_write_through.py — Iter 313

RCA REPRO TEST (written FIRST, before the fix).

The founder observed on 2026-07-27 that /api/aurem-dev/version
returned the SAME `commit_sha` ("34e9731265cf") across two
distinct deploys (Iter 311 at 00:19 UTC, Iter 312 at 01:52 UTC),
even though the `built_at` timestamp legitimately changed.

Root cause: `routers/version.py::_read_commit()` cascades through:
  (1) explicit env vars → (2) `.emergent/emergent.yml` `job_id` →
  (3) `backend/BUILD_INFO.txt` → (4) `git rev-parse HEAD`.

Emergent's deploy pipeline strips `.git` from the container, so
cascade step (4) always fails in production. Cascade step (3) —
the `BUILD_INFO.txt` static marker — is documented in the code
comments as the intended escape hatch, but NOTHING IN THE CODEBASE
EVER WRITES IT. It's dead code. So prod lands on step (2), which
is `emergent.yml`'s `job_id` — a per-JOB identifier that is stable
across deploys of the same job (only rotates when the Emergent job
itself is recreated). Result: /version reports the same SHA deploy
after deploy, breaking the "did the code actually change on prod?"
diagnostic that every future deploy verification depends on.

Fix (Iter 313): when cascade step (4) `git rev-parse HEAD`
succeeds (which happens in preview where `.git` still exists),
side-effect write the real SHA to `backend/BUILD_INFO.txt`.
Emergent's deploy pipeline bundles the whole `backend/` folder
into the prod snapshot, so the freshly-written marker travels
with the deploy. When prod boots without `.git`, cascade step (4)
fails but step (3) now returns the SHA captured on the last
preview backend restart — which IS the SHA being deployed, because
backend hot-reload re-imports `version.py` on every relevant code
change, running `_read_commit()` again each time.

TEST DISCIPLINE:
  1. `test_repro_build_info_txt_not_written` — asserts the current
     code path does NOT write BUILD_INFO.txt when `_read_commit()`
     succeeds via git. MUST FAIL after Iter 313 (fix makes it write).
  2. `test_git_read_writes_build_info_marker` — after the fix,
     invoking `_read_commit()` in an environment where `git` works
     must leave the real SHA in `backend/BUILD_INFO.txt` at the
     path exposed to the container image.
  3. `test_no_git_falls_through_to_build_info` — simulates prod
     (no .git, no env vars, only BUILD_INFO.txt present) and asserts
     the SHA read matches the marker content — proving the escape
     hatch actually fires.
  4. `test_env_var_still_wins` — regression: an explicit
     `AUREM_COMMIT_SHA` env var must still short-circuit the cascade
     and NOT trigger the BUILD_INFO.txt write (env-var users have
     their own truth source; we don't touch the marker for them).
"""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


_VERSION_MODULE_PATH = Path("/app/backend/routers/version.py")
_BUILD_INFO_PATH     = Path("/app/backend/BUILD_INFO.txt")


def _reload_version_module():
    """Force a fresh import of routers.version so its module-level
    `_COMMIT_SHA = _read_commit()` runs against the current env."""
    import importlib
    import routers.version as _v
    importlib.reload(_v)
    return _v


# ── 1. REPRO — BUILD_INFO.txt is NEVER written today ────────────────
def test_repro_build_info_txt_not_written_by_current_code():
    """
    Grep the version.py source for any code path that writes to
    BUILD_INFO.txt. Before Iter 313 there is NONE — the escape hatch
    at cascade step (3) is documented but dead.

    MUST FAIL against current code (proves the write path doesn't
    exist). MUST PASS after Iter 313 (write path added inside the
    git-success branch of `_read_commit`).
    """
    src = _VERSION_MODULE_PATH.read_text()
    # Look for ANY of the plausible write-through patterns:
    #   BUILD_INFO.txt      (literal filename write)
    #   BUILD_INFO_PATH     (module-level constant write)
    #   marker.write_text   (path-object write on the marker var)
    has_write = any(pat in src for pat in (
        'BUILD_INFO.txt".write_text',       # Path("...BUILD_INFO.txt").write_text(...)
        'BUILD_INFO.txt").write_text',      # same, single-quote variant
        "BUILD_INFO_MARKER.write_text",     # module-level constant
        "_BUILD_INFO_MARKER.write_text",
        "marker.write_text",                # local var pattern
        "_write_build_info(",               # helper function
        ".write_text(sha",                  # any .write_text(sha...)
    ))
    assert has_write, (
        "REPRO: routers/version.py does not contain any code that "
        "WRITES BUILD_INFO.txt. Cascade step (3) is documented as "
        "the escape hatch for prod (where .git is stripped), but "
        "nothing populates it. Iter 313 must add a write inside "
        "the git-success branch of _read_commit()."
    )


# ── 2. Fix behavior — git success writes the marker ─────────────────
def test_git_read_writes_build_info_marker(tmp_path, monkeypatch):
    """
    After the fix, when _read_commit() successfully reads a SHA via
    `git rev-parse HEAD`, it must write that SHA to
    `backend/BUILD_INFO.txt` (12-char form, no newline noise).
    Enforcement: point the BUILD_INFO write target at a tmp file
    via monkeypatch, run _read_commit, assert the tmp file now
    holds the SHA that _read_commit returned.
    """
    # Snapshot + isolate.
    marker_tmp = tmp_path / "BUILD_INFO.txt"
    if marker_tmp.exists():
        marker_tmp.unlink()

    # Clear cascade-earlier signals so we exercise the git branch.
    for k in ("AUREM_COMMIT_SHA", "GIT_COMMIT_SHA",
              "EMERGENT_JOB_ID", "EMERGENT_DEPLOY_ID"):
        monkeypatch.delenv(k, raising=False)

    # IMPORTANT — reload FIRST so we have a fresh module, THEN
    # patch its attributes. importlib.reload re-runs the module's
    # top-level statements which would otherwise overwrite our
    # patched marker/yaml-candidate constants.
    v = _reload_version_module()

    # Point the fix's write target at the tmp path.
    monkeypatch.setattr(
        v, "_BUILD_INFO_MARKER", marker_tmp,
        raising=False,
    )
    # Also block cascade step (4) `.emergent/emergent.yml` fallback.
    monkeypatch.setattr(
        v, "_EMERGENT_YAML_CANDIDATES", (), raising=False,
    )
    # If git works in the test env, we should have a 12-char SHA
    # AND the marker file should now exist with that SHA.
    sha_from_git = None
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd="/app/backend", timeout=2,
            stderr=subprocess.DEVNULL,
        )
        sha_from_git = out.decode().strip()[:12]
    except Exception:
        pytest.skip("git not available in this test env — cannot "
                    "exercise the git-success write branch.")

    got_sha = v._read_commit()
    assert got_sha == sha_from_git, (
        f"_read_commit() returned {got_sha!r}, expected git-derived "
        f"{sha_from_git!r}. The git branch of the cascade is broken."
    )
    assert marker_tmp.exists(), (
        "Iter 313 fix must write BUILD_INFO.txt when _read_commit "
        "succeeds via git. Marker file does not exist."
    )
    marker_content = marker_tmp.read_text().strip()
    assert marker_content == sha_from_git, (
        f"Marker file content {marker_content!r} != git SHA "
        f"{sha_from_git!r}. Write must be authoritative."
    )


# ── 3. Prod-shape simulation — no git, marker fallback fires ────────
def test_no_git_falls_through_to_build_info_marker(tmp_path, monkeypatch):
    """
    Simulate the prod container shape: no `.git`, no env vars, but
    `backend/BUILD_INFO.txt` present with a known SHA (as it would
    be after a preview build wrote it). _read_commit MUST return
    the marker's SHA.
    """
    known_sha = "abcdef123456"
    marker_tmp = tmp_path / "BUILD_INFO.txt"
    marker_tmp.write_text(known_sha)

    # Clear cascade-earlier signals.
    for k in ("AUREM_COMMIT_SHA", "GIT_COMMIT_SHA",
              "EMERGENT_JOB_ID", "EMERGENT_DEPLOY_ID"):
        monkeypatch.delenv(k, raising=False)

    # Reload FIRST (see test_git_read_writes_build_info_marker for
    # the rationale), THEN patch, THEN call _read_commit.
    v = _reload_version_module()

    monkeypatch.setattr(
        v, "_BUILD_INFO_MARKER", marker_tmp,
        raising=False,
    )
    monkeypatch.setattr(
        v, "_EMERGENT_YAML_CANDIDATES", (), raising=False,
    )
    # Force cascade step (2) `git rev-parse HEAD` to fail — simulate
    # the .git-stripped prod container.
    def _fake_check_output(*_a, **_kw):
        raise subprocess.CalledProcessError(128, ["git"])
    monkeypatch.setattr(
        "routers.version.subprocess.check_output",
        _fake_check_output,
    )

    got_sha = v._read_commit()
    assert got_sha == known_sha, (
        f"With .git stripped and marker present, _read_commit must "
        f"return the marker content. Got {got_sha!r}, expected "
        f"{known_sha!r}."
    )


# ── 4. Regression — env var still short-circuits, no marker write ───
def test_env_var_still_wins_and_no_marker_write(tmp_path, monkeypatch):
    """
    Explicit `AUREM_COMMIT_SHA` env var users have their own truth
    source (CI injection). The fix must not touch BUILD_INFO.txt
    when we're using their env value — that would create a stale
    marker for the next non-CI process.
    """
    env_sha = "envfeedface1"
    monkeypatch.setenv("AUREM_COMMIT_SHA", env_sha)

    marker_tmp = tmp_path / "BUILD_INFO.txt"
    if marker_tmp.exists():
        marker_tmp.unlink()

    v = _reload_version_module()
    monkeypatch.setattr(
        v, "_BUILD_INFO_MARKER", marker_tmp,
        raising=False,
    )

    got_sha = v._read_commit()
    assert got_sha == env_sha, (
        f"Env-var short-circuit broken. Expected {env_sha!r}, got "
        f"{got_sha!r}."
    )
    assert not marker_tmp.exists(), (
        "Regression: _read_commit wrote BUILD_INFO.txt while an "
        "env-var short-circuit was active. The marker is only for "
        "the git-derived cascade branch."
    )
