"""Iter 333 · Phase 1 — Persistent Correction Rules (shadow-first).

Founder-locked design: manual /rule slash command only (no LLM
detection), applies_to_paths globs, max 10 rules per prompt,
per-project enforce toggle default OFF (= shadow), instrumented
metric via correction_rule_events.

Behavioral tests run against REAL local Mongo (founder standing rule:
real-Mongo tests when possible), on a throwaway db dropped per run.
"""
import os
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from services import correction_rules as cr

ENGINE_SRC = Path("/app/backend/services/loop_engine.py").read_text()
SLASH_SRC = Path("/app/backend/services/ora_chat/slash_commands.py").read_text()

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
TEST_DB = "test_iter333_correction_rules"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(MONGO_URL)
    await client.drop_database(TEST_DB)
    yield client[TEST_DB]
    await client.drop_database(TEST_DB)
    client.close()


# ─── Pure functions ─────────────────────────────────────────────────

class TestMatchRules:
    def _rule(self, rid, globs):
        return {"rule_id": rid, "instruction": f"rule {rid}",
                "applies_to_paths": globs}

    def test_glob_scoping(self):
        rules = [self._rule("r1", ["src/*"]), self._rule("r2", ["*.md"])]
        out = cr.match_rules(rules, ["src/app.py", "README.md", "lib/x.js"])
        by_id = {m["rule"]["rule_id"]: m["matched_paths"] for m in out}
        assert by_id["r1"] == ["src/app.py"]
        assert by_id["r2"] == ["README.md"]

    def test_empty_globs_match_all(self):
        out = cr.match_rules([self._rule("r1", [])], ["a.py", "b.js"])
        assert out[0]["matched_paths"] == ["a.py", "b.js"]

    def test_no_match_excluded(self):
        out = cr.match_rules([self._rule("r1", ["tests/*"])], ["src/a.py"])
        assert out == []

    def test_max_10_rules_per_prompt(self):
        rules = [self._rule(f"r{i}", []) for i in range(15)]
        out = cr.match_rules(rules, ["a.py"])
        assert len(out) == cr.MAX_RULES_PER_PROMPT == 10

    def test_empty_paths_no_matches(self):
        assert cr.match_rules([self._rule("r1", [])], []) == []


class TestBuildRulesBlock:
    def test_block_format(self):
        matches = [{"rule": {"rule_id": "r1",
                             "instruction": "never use console.log"},
                    "matched_paths": ["a.js"]}]
        block = cr.build_rules_block(matches)
        assert block.startswith("PERSISTENT CORRECTION RULES")
        assert "1. never use console.log" in block
        assert block.endswith("\n\n")

    def test_empty_matches_empty_string(self):
        assert cr.build_rules_block([]) == ""


class TestParseAddArgs:
    def test_with_paths(self):
        ins, globs = cr.parse_add_args(
            "always use yarn paths: frontend/*, package.json")
        assert ins == "always use yarn"
        assert globs == ["frontend/*", "package.json"]

    def test_without_paths(self):
        ins, globs = cr.parse_add_args("never touch tests")
        assert ins == "never touch tests"
        assert globs == []


# ─── Real-Mongo CRUD + shadow metric ────────────────────────────────

class TestCrudAndMetric:
    async def test_add_list_delete_roundtrip(self, db):
        res = await cr.add_rule(db, "u1", "p1", "use tabs",
                                ["src/*"])
        assert res["ok"] is True
        rid = res["rule"]["rule_id"]
        rules = await cr.list_rules(db, "u1", "p1")
        assert len(rules) == 1 and rules[0]["rule_id"] == rid
        assert rules[0]["active"] is True
        assert await cr.delete_rule(db, "u1", rid) is True
        assert await cr.list_rules(db, "u1", "p1") == []

    async def test_instruction_validation(self, db):
        assert (await cr.add_rule(db, "u1", "p1", ""))["ok"] is False
        assert (await cr.add_rule(db, "u1", "p1", "x" * 301))["ok"] is False

    async def test_enforce_default_off_shadow(self, db):
        assert await cr.get_enforce(db, "u1", "p1") is False
        await cr.set_enforce(db, "u1", "p1", True)
        assert await cr.get_enforce(db, "u1", "p1") is True
        await cr.set_enforce(db, "u1", "p1", False)
        assert await cr.get_enforce(db, "u1", "p1") is False

    async def test_record_events_metric(self, db):
        res = await cr.add_rule(db, "u1", "p1", "no print()", [])
        rule = res["rule"]
        matches = cr.match_rules([rule], ["a.py", "b.py"])
        await cr.record_rule_events(
            db, loop_id="loop_1", user_id="u1", project_id="p1",
            phase="execute", matches=matches, mode="shadow")
        ev = await db.correction_rule_events.find_one({}, {"_id": 0})
        assert ev["mode"] == "shadow" and ev["loop_id"] == "loop_1"
        assert ev["matched_paths"] == ["a.py", "b.py"]
        updated = (await cr.list_rules(db, "u1", "p1"))[0]
        assert updated["hits"] == 1 and updated["last_hit_at"]
        rep = await cr.rule_report(db, "u1", "p1")
        assert rep["total_matches"] == 1 and rep["loops_affected"] == 1


# ─── Slash command handler ──────────────────────────────────────────

class TestSlashRule:
    async def _run(self, db, args, user_id="u1"):
        from services.ora_chat import slash_commands as sc
        import unittest.mock as um
        with um.patch.object(sc, "get_db", lambda: db):
            return await sc.run_slash_command("rule", args,
                                              {"user_id": user_id})

    async def test_rule_registered_in_known_commands(self, db):
        from services.ora_chat.safety import KNOWN_COMMANDS
        assert "rule" in KNOWN_COMMANDS

    async def test_add_and_list_via_slash(self, db):
        r = await self._run(db, "add use yarn only paths: frontend/*")
        assert r["ok"] is True
        assert r["value"]["instruction"] == "use yarn only"
        r2 = await self._run(db, "list")
        assert r2["ok"] is True and len(r2["value"]) == 1
        assert "shadow" in r2["metric"]

    async def test_on_off_toggle(self, db):
        r = await self._run(db, "on")
        assert r["ok"] is True and r["value"]["enforce"] is True
        r2 = await self._run(db, "list")
        assert "ENFORCE" in r2["metric"]
        r3 = await self._run(db, "off")
        assert r3["value"]["enforce"] is False

    async def test_usage_on_unknown_sub(self, db):
        r = await self._run(db, "")
        assert r["ok"] is True and "Usage" in r["metric"]

    async def test_unauthenticated_refused(self, db):
        from services.ora_chat import slash_commands as sc
        import unittest.mock as um
        with um.patch.object(sc, "get_db", lambda: db):
            r = await sc.run_slash_command("rule", "list", {})
        assert r["ok"] is False and r["error"] == "unauthenticated"


# ─── Engine wiring (source-level locks) ─────────────────────────────

class TestEngineWiring:
    def test_execute_loads_and_records_shadow(self):
        seg = ENGINE_SRC.split("async def _do_execute")[1].split(
            "async def _do_verify")[0]
        assert "correction_rules" in seg
        assert "record_rule_events" in seg
        assert 'phase="execute"' in seg

    def test_injection_gated_on_enforce_only(self):
        seg = ENGINE_SRC.split("async def _do_execute")[1].split(
            "async def _do_verify")[0]
        assert '_cr_mode == "enforce"' in seg
        assert "build_rules_block" in seg

    def test_fail_open_wrapper(self):
        seg = ENGINE_SRC.split("Persistent Correction Rules")[1][:3000]
        assert "except Exception" in seg

    def test_no_llm_correction_detection(self):
        """Binding founder correction: rules come ONLY from the manual
        slash command — no LLM detects/creates them."""
        src = Path("/app/backend/services/correction_rules.py").read_text()
        assert "call_llm" not in src and "openrouter" not in src.lower()
        assert '"source":           "slash_command"' in src
