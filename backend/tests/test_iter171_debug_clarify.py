"""
Iter 171 — Mode D clarifies on vague debug requests.

Pre-fix: typing "I saw some issues in X, can you debug?" returned the
canned "ROOT CAUSE: insufficient signal to diagnose" template. Now we
short-circuit BEFORE the LLM call and ask the user for the specific
context we need.
"""
from __future__ import annotations
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.mode_d_debugger import (    # noqa: E402
    has_concrete_debug_signal,
    is_debug_request,
    run_debug_session,
)


class TestConcreteSignalDetection(unittest.TestCase):
    def test_intent_only_returns_false(self):
        for msg in [
            "can you debug this",
            "please diagnose",
            "investigate the issue",
            "debug it",
            "I saw some issues in hello can you debug and show me",
        ]:
            self.assertFalse(
                has_concrete_debug_signal(msg),
                f"intent-only message wrongly flagged as concrete: {msg!r}",
            )

    def test_concrete_signal_returns_true(self):
        for msg in [
            "got a TypeError when clicking save",
            "API returns 500 on /login",
            "Traceback (most recent call last): File \"x.py\", line 5",
            "CORS error blocked the request",
            "[object Object] showing in toast",
            "got ECONNREFUSED on port 5432",
            "f12 says undefined is not a function",
            "Cannot read properties of null at App.jsx:88",
        ]:
            self.assertTrue(
                has_concrete_debug_signal(msg),
                f"concrete signal missed: {msg!r}",
            )

    def test_is_debug_request_still_matches_intent(self):
        # Iter 212f — bare verb "debug" no longer fires Mode D on its
        # own (was burning LLM calls only to bail with "insufficient
        # signal to diagnose"). It now only fires Mode D when paired
        # with a SOFT error signal (error/bug/broken/etc.) in the
        # same message. "debug this please" lacks any such signal so
        # is_debug_request returns False — the router will send it
        # to Mode A (general chat) which can ask "what would you like
        # me to debug?" instead.
        self.assertFalse(is_debug_request("debug this please"))
        # Paired with an actual error term, it still fires:
        self.assertTrue(is_debug_request("debug this error please"))


class TestClarifyShortCircuit(unittest.IsolatedAsyncioTestCase):
    async def test_vague_debug_request_returns_clarify(self):
        fake_db = MagicMock()
        fake_db.ora_logs = MagicMock()
        fake_db.ora_logs.insert_one = AsyncMock()
        # Also stub any other collections log_conversational might touch.
        fake_db.aurem_messages = MagicMock()
        fake_db.aurem_messages.insert_one = AsyncMock()

        result = await run_debug_session(
            db=fake_db,
            user_message="I saw some issues in hello can you debug and show me",
            repo_owner="x",
            repo_name="y",
            repo_ctx="",
            f12_payload=None,
            github_pat=None,
        )
        # Must NOT be the canned bail template
        self.assertNotIn("insufficient signal to diagnose", result["ora_reply"].lower())
        self.assertNotIn("reproduce the error with a real stack trace",
                         result["ora_reply"].lower())
        # Must be the clarify path
        self.assertTrue(result.get("clarify"))
        self.assertFalse(result["can_auto_fix"])
        self.assertIn("F12", result["ora_reply"])
        self.assertIn("screenshot", result["ora_reply"].lower())

    async def test_concrete_signal_skips_clarify(self):
        # A message WITH a concrete signal should NOT short-circuit —
        # it should proceed into the fast-path / LLM diagnosis.
        # We assert by checking the clarify flag is absent / False.
        fake_db = MagicMock()
        fake_db.ora_logs = MagicMock()
        fake_db.ora_logs.insert_one = AsyncMock()
        fake_db.aurem_messages = MagicMock()
        fake_db.aurem_messages.insert_one = AsyncMock()

        # Mock llm_diagnosis to avoid network — assert we GOT there.
        from services import mode_d_debugger as mdd
        called = {"hit": False}

        async def fake_llm(*_a, **_k):
            called["hit"] = True
            return {"cause": "fake", "fix_suggestion": "ok",
                    "files_to_check": [], "severity": "low",
                    "needs_commit": False}

        orig = mdd.llm_diagnosis
        mdd.llm_diagnosis = fake_llm
        try:
            r = await run_debug_session(
                db=fake_db,
                user_message="got a TypeError: cannot read properties of null at App.jsx:88",
                repo_owner="x", repo_name="y", repo_ctx="",
            )
            self.assertFalse(r.get("clarify", False))
            self.assertTrue(called["hit"], "llm_diagnosis should have been reached")
        finally:
            mdd.llm_diagnosis = orig


if __name__ == "__main__":
    unittest.main()
