"""Iter 328 · tests for services/inventory_service.py

Tests the git-diff scan detects added routers, envvars, loop-run-log
kinds, and fails open on git errors.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services import inventory_service as isvc


@pytest.fixture
def tmp_repo(tmp_path, monkeypatch):
    """Init a tiny git repo we can commit into, then point the service
    at it via the private _REPO_ROOT."""
    d = tmp_path / "repo"
    d.mkdir()
    (d / "backend").mkdir()
    (d / "backend" / "routers").mkdir()
    (d / "backend" / "services").mkdir()
    (d / "backend" / "scripts").mkdir()
    # Copy the append script so isvc's _inv_append import works even in
    # the tmp env — but the actual append target file lives at
    # /app/memory/SYSTEM_INVENTORY.md, so we monkeypatch that too.
    fake_inv = tmp_path / "memory" / "SYSTEM_INVENTORY.md"
    fake_inv.parent.mkdir()
    fake_inv.write_text("# Fake\n")

    def _run(*args, cwd=str(d)):
        subprocess.run(args, cwd=cwd, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    _run("git", "init", "-q")
    _run("git", "config", "user.email", "x@y.z")
    _run("git", "config", "user.name", "test")
    _run("git", "commit", "-q", "--allow-empty", "-m", "root")
    # Base ref = root commit
    base = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(d)).decode().strip()

    monkeypatch.setattr(isvc, "_REPO_ROOT", d)
    if isvc._inv_append is not None:
        monkeypatch.setattr(isvc._inv_append, "INV", fake_inv)
    return d, base, _run


def test_scan_detects_new_router(tmp_repo):
    d, base, run = tmp_repo
    (d / "backend" / "routers" / "shiny.py").write_text(
        'from fastapi import APIRouter\n'
        'router = APIRouter(prefix="/shiny")\n'
        '@router.get("/hello")\n'
        'def hello(): return {"ok": True}\n'
        '@router.post("/echo")\n'
        'def echo(): return {"ok": True}\n'
    )
    run("git", "add", ".")
    run("git", "commit", "-q", "-m", "add shiny")
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(d)).decode().strip()
    changes = isvc.scan_git_range(base, head)
    router_hits = [c for c in changes if c["kind"] == "router"]
    assert len(router_hits) == 1
    assert router_hits[0]["path"] == "routers/shiny.py"
    assert router_hits[0]["prefix"] == "/shiny"
    assert router_hits[0]["routes"] == 2


def test_scan_detects_new_envvar(tmp_repo):
    d, base, run = tmp_repo
    (d / "backend" / "services" / "cfg.py").write_text(
        'import os\n'
        'FOO = os.environ.get("BRAND_NEW_ENV_VAR", "")\n'
    )
    run("git", "add", ".")
    run("git", "commit", "-q", "-m", "add cfg")
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(d)).decode().strip()
    changes = isvc.scan_git_range(base, head)
    env_hits = [c for c in changes if c["kind"] == "envvar"]
    assert any(c["name"] == "BRAND_NEW_ENV_VAR" for c in env_hits)


def test_scan_detects_loop_run_log_kind(tmp_repo):
    d, base, run = tmp_repo
    (d / "backend" / "services" / "engine.py").write_text(
        'async def log(db):\n'
        '    await db.loop_run_log.insert_one({\n'
        '        "kind": "iter328_new_kind", "ts": 0,\n'
        '    })\n'
    )
    run("git", "add", ".")
    run("git", "commit", "-q", "-m", "add engine")
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(d)).decode().strip()
    changes = isvc.scan_git_range(base, head)
    kinds = [c for c in changes if c["kind"] == "loop_run_log_kind"]
    assert any(c["value"] == "iter328_new_kind" for c in kinds)


def test_scan_empty_on_bad_git(monkeypatch):
    monkeypatch.setattr(isvc, "_REPO_ROOT", Path("/no/such/dir"))
    assert isvc.scan_git_range("HEAD~1", "HEAD") == []


def test_record_from_git_appends_new_router(tmp_repo):
    d, base, run = tmp_repo
    (d / "backend" / "routers" / "beta.py").write_text(
        'from fastapi import APIRouter\n'
        'router = APIRouter(prefix="/beta")\n'
        '@router.get("/x")\n'
        'def x(): return 1\n'
    )
    run("git", "add", ".")
    run("git", "commit", "-q", "-m", "add beta")
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(d)).decode().strip()
    r = isvc.record_from_git(base, head, iter_num=999)
    assert "error" not in r
    # Should have appended at least one entry (router).
    assert any("router:routers/beta.py" in m for m in r.get("appended", []))


def test_record_from_git_async_never_raises(tmp_repo):
    import asyncio
    d, base, run = tmp_repo
    # Point at a bogus range — this should silently return, not raise.
    asyncio.run(isvc.record_from_git_async("nope", "invalid", iter_num=1))
