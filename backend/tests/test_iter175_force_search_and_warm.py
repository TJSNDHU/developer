"""
Iter 175 — Mode D FORCE search + Orchestrator independent fallbacks.

Covers:
  • _extract_service_name() — known service vocabulary detection
  • run_debug_session() — vague debug + known service → force_search return
                          (no "insufficient signal" wall)
  • run_debug_session() — vague debug + NO service → clarify (iter 171
                          behaviour preserved)
"""
from __future__ import annotations
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.mode_d_debugger import (    # noqa: E402
    _extract_service_name,
    run_debug_session,
)


# ─────────────────────────────────────────────────────────────────────
# Service-name extraction
# ─────────────────────────────────────────────────────────────────────
class TestExtractServiceName(unittest.TestCase):
    def test_third_party_saas(self):
        cases = {
            "I saw some issues in twilio can you debug":           "twilio",
            "stripe webhook failing on prod":                       "stripe",
            "github oauth flow broken":                             "github",
            "Mongodb queries are slow":                             "mongodb",
            "sendgrid emails not delivering":                       "sendgrid",
            "openai API returning 429":                             "openai",
        }
        for msg, expected in cases.items():
            self.assertEqual(_extract_service_name(msg), expected, msg)

    def test_protocols_and_auth(self):
        cases = {
            "jwt token validation failing":  "jwt",
            "cors error in browser":         "cors",
            "websocket disconnecting":       "websocket",
            "graphql query taking forever":  "graphql",
        }
        for msg, expected in cases.items():
            self.assertEqual(_extract_service_name(msg), expected, msg)

    def test_conceptual_buckets(self):
        cases = {
            "auth not working":              "auth",
            "bug in the payment flow":       "payment",
            "checkout button is broken":     "checkout",
            "deploy keeps failing":          "deploy",
        }
        for msg, expected in cases.items():
            self.assertEqual(_extract_service_name(msg), expected, msg)

    def test_no_service_returns_empty(self):
        for msg in [
            "debug this",
            "can you investigate?",
            "make it faster",
            "fix the thing",
            "please diagnose",
            "",
            None,
        ]:
            self.assertEqual(
                _extract_service_name(msg or ""),
                "",
                f"unexpected service from: {msg!r}",
            )

    def test_first_match_wins(self):
        # "auth" appears first alphabetically but "stripe" first in
        # the message — we want the FIRST in-message match.
        self.assertEqual(
            _extract_service_name("stripe payment auth flow"),
            "stripe",
        )


# ─────────────────────────────────────────────────────────────────────
# Force-search behaviour in run_debug_session
# ─────────────────────────────────────────────────────────────────────
class TestForceSearchBail(unittest.IsolatedAsyncioTestCase):
    async def test_twilio_returns_force_search_not_clarify(self):
        # This is the EXACT prompt the user reported in the original
        # screenshot. Pre-iter-175 → canned "insufficient signal" wall.
        fake_db = MagicMock()
        fake_db.ora_logs = MagicMock()
        fake_db.ora_logs.insert_one = AsyncMock()
        fake_db.aurem_messages = MagicMock()
        fake_db.aurem_messages.insert_one = AsyncMock()

        r = await run_debug_session(
            db=fake_db,
            user_message="i saw some issues in twilio can you debug and show me",
            repo_owner="x", repo_name="y", repo_ctx="",
        )

        # Must short-circuit with force_search
        self.assertEqual(r.get("action"), "force_search")
        self.assertEqual(r.get("query"), "twilio")
        # Reply must reference the service by name + Mode A routing
        body = r["ora_reply"]
        self.assertIn("twilio", body.lower())
        self.assertIn("read", body.lower())
        # And must NOT be the canned bail wall
        self.assertNotIn("insufficient signal", body.lower())
        self.assertNotIn("real stack trace", body.lower())
        # Clarify flag must be False — this is NOT a clarify path
        self.assertFalse(r.get("clarify", False))

    async def test_vague_with_no_service_still_clarifies(self):
        # Iter 171 behaviour preserved: when the user gives no service
        # AND no signal, we still clarify (not force_search).
        fake_db = MagicMock()
        fake_db.ora_logs = MagicMock()
        fake_db.ora_logs.insert_one = AsyncMock()
        fake_db.aurem_messages = MagicMock()
        fake_db.aurem_messages.insert_one = AsyncMock()

        r = await run_debug_session(
            db=fake_db,
            user_message="i saw some issues can you debug",
            repo_owner="x", repo_name="y", repo_ctx="",
        )
        self.assertNotEqual(r.get("action"), "force_search")
        self.assertTrue(r.get("clarify"))
        self.assertIn("F12", r["ora_reply"])

    async def test_concrete_signal_skips_force_search(self):
        # A real stack trace WITH a service mention — still goes to
        # llm_diagnosis (not force_search), because we have enough to
        # diagnose immediately.
        from services import mode_d_debugger as mdd

        fake_db = MagicMock()
        fake_db.ora_logs = MagicMock()
        fake_db.ora_logs.insert_one = AsyncMock()
        fake_db.aurem_messages = MagicMock()
        fake_db.aurem_messages.insert_one = AsyncMock()

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
                user_message="twilio TypeError: cannot read properties of null at App.jsx:88",
                repo_owner="x", repo_name="y", repo_ctx="",
            )
            self.assertNotEqual(r.get("action"), "force_search")
            self.assertTrue(called["hit"], "llm_diagnosis should be reached")
        finally:
            mdd.llm_diagnosis = orig


if __name__ == "__main__":
    unittest.main()
