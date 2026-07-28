"""Iter 331 · NoneType.get crash class — source-level locks.

Founder-reported prod crash: pasting a long error-report into chat made
ORA reply with "'NoneType' object has no attribute 'get'". Two traps:

1. `x.get("k", {}).get(...)` — the default only applies when the key is
   MISSING; a present key with value None returns None and the chained
   .get crashes. (LLMs can emit tool calls with `args: null`.)
2. chat_stream trusted `ev["result"]` to be a dict.
"""
import re
from pathlib import Path

CHAT = Path("/app/backend/routers/chat.py").read_text(encoding="utf-8")
ORCH = Path("/app/backend/services/orchestrator.py").read_text(encoding="utf-8")

TRAP = re.compile(r"""\.get\((['"]\w+['"]),\s*\{\}\)\s*\.""")


def test_no_chained_get_default_dict_trap_in_orchestrator():
    hits = [m.group(0) for m in TRAP.finditer(ORCH)]
    assert hits == [], f"trap pattern .get('k', {{}}). found: {hits}"


def test_no_chained_get_default_dict_trap_in_chat_router():
    hits = [m.group(0) for m in TRAP.finditer(CHAT)]
    assert hits == [], f"trap pattern .get('k', {{}}). found: {hits}"


def test_stream_result_none_guard_present():
    assert "if not isinstance(result, dict):" in CHAT
    assert '"pipeline returned no result"' in CHAT
