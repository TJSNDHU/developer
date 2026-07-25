"""Additional focused regression checks for iter 308 loop-stuck RCA.

These tests are intentionally narrow: they verify that a long Execute gather
emits a Mongo-persisted heartbeat and that main.py's stale-loop reaper is a
periodic loop, not the old single startup call.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from services import loop_engine


class _FakeCollection:
    def __init__(self):
        self.updates = []
        self.inserts = []

    async def update_one(self, query, update, upsert=False):  # noqa: ANN001
        self.updates.append({"query": query, "update": update, "upsert": upsert})

    async def insert_one(self, doc):  # noqa: ANN001
        self.inserts.append(doc)

    async def find_one(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


class _FakeDB:
    def __init__(self):
        self.loop_sessions = _FakeCollection()
        self.loop_run_log = _FakeCollection()
        self.loop_events = _FakeCollection()


class _BinCtx:
    repo_owner = "owner"
    repo_name = "repo"
    branch = "main"
    pat = "ghp_fake"


@pytest.mark.asyncio
async def test_execute_gather_emits_persisted_heartbeat(monkeypatch):
    """A long code-generation gather must emit visible EXECUTING keepalives."""

    async def _no_file_selector(**_kwargs):
        return {}

    async def _frozen_spec(_db, _loop_id):
        return {"frozen_files_to_change": ["a.py", "b.py"]}

    async def _fetch_file(*_args, **_kwargs):
        return "print('old')\n"

    class _SlowParliament:
        def __init__(self, db=None):  # noqa: ANN001
            self.db = db

        async def run(self, task, context):  # noqa: ANN001
            # The new gather-level heartbeat fires at 10s. Sleeping just over
            # that proves the user does not stare at a stale EXECUTE START line.
            await asyncio.sleep(10.2)
            return {"status": "success", "output": "print('new')\n"}

    import services.file_selector as file_selector
    import services.github_api_writer as github_api_writer
    from services import loop_task_specs
    import core.parliament as parliament

    monkeypatch.setattr(file_selector, "select_relevant_files", _no_file_selector)
    monkeypatch.setattr(loop_task_specs, "get", _frozen_spec)
    monkeypatch.setattr(github_api_writer, "fetch_file", _fetch_file)
    monkeypatch.setattr(parliament, "Parliament", _SlowParliament)

    db = _FakeDB()
    engine = loop_engine.LoopEngine(
        db=db,
        loop_id="loop_test_hb",
        user_id="u_test",
        project_id="p_test",
        user_message="change two files",
        bin_ctx=_BinCtx(),
    )
    engine.context["plan"] = {
        "title": "Test plan",
        "bullets": ["change two files"],
        "files_to_change": ["a.py", "b.py"],
    }

    await engine._do_execute()

    emitted = []
    while not engine.queue.empty():
        emitted.append(await engine.queue.get())

    gather_heartbeats = [
        ev for ev in emitted
        if ev.get("state") == "executing"
        and ev.get("phase") == "execute"
        and ev.get("data", {}).get("sub_step") == "heartbeat"
        and ev.get("data", {}).get("keepalive") is True
        and "hb_tick" in ev.get("data", {})
    ]
    assert gather_heartbeats, "expected gather-level EXECUTING heartbeat with hb_tick"
    assert "Generating 2 file(s)" in gather_heartbeats[0]["message"]

    persisted_last_events = [
        u["update"].get("$set", {}).get("last_event")
        for u in db.loop_sessions.updates
    ]
    assert any(
        ev and ev.get("data", {}).get("hb_tick")
        for ev in persisted_last_events
    ), "heartbeat must be persisted as loop_sessions.last_event for cross-worker SSE clients"


def test_main_resume_stale_loops_is_periodic_not_single_run():
    source = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(source.read_text())

    class _Finder(ast.NodeVisitor):
        found = False

        def visit_AsyncFunctionDef(self, node):  # noqa: ANN001
            if node.name == "_resume_stale_loops":
                has_while_true = any(
                    isinstance(child, ast.While)
                    and isinstance(child.test, ast.Constant)
                    and child.test.value is True
                    for child in ast.walk(node)
                )
                calls_resume_stale = any(
                    isinstance(child, ast.Call)
                    and getattr(child.func, "id", getattr(child.func, "attr", "")) == "resume_stale"
                    for child in ast.walk(node)
                )
                sleeps_60 = any(
                    isinstance(child, ast.Call)
                    and getattr(child.func, "attr", "") == "sleep"
                    and child.args
                    and isinstance(child.args[0], ast.Constant)
                    and child.args[0].value == 60
                    for child in ast.walk(node)
                )
                self.found = has_while_true and calls_resume_stale and sleeps_60
            self.generic_visit(node)

    finder = _Finder()
    finder.visit(tree)
    assert finder.found, "_resume_stale_loops must keep sweeping resume_stale every 60s"