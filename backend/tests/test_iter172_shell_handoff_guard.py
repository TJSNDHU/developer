"""
Iter 172 — Shell-command aurem-handoff guards (UPDATED in Iter 212m-26).

Failure mode being closed:
   AUREM LLM emits an `aurem-handoff` fence containing
     {"command": "pip install twilio", "files": []}
   The persona EXPLICITLY forbids this (handoffs are for file edits;
   shell commands run via `execute_bash`). When the user replies with
   "install" / "do it" / "do it fix the issue properly", the
   orchestrator would try to enqueue the shell command as a CTO task.
   The worker hangs because there are no files to commit → user sees
   "thinking · 365.4s" → rage-quit.

Layers of defense remaining after Iter 212m-26:
  1. `_handoff_brief_is_shell_command()` recogniser — covers raw
     ("pip install X"), JSON ({"command":"pip install X","files":[]}),
     and a wide array of package-manager / system commands.
  2. `_maybe_guard_shell_handoff_followup()` catches short follow-ups
     ("do it fix the issue properly", "now install", "make it work")
     BEFORE they reach the expensive orchestrator.

Iter 212m-26 NOTE: the second layer — `_maybe_ship_shortcut()` — was
DELETED along with the entire auto-ship path. Shipping now requires
a manual click on the "🚀 Ship via CTO" button. Tests covering that
removed layer are pruned below.
"""
from __future__ import annotations
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routers.chat import (    # noqa: E402
    _handoff_brief_is_shell_command,
    _maybe_guard_shell_handoff_followup,
)


# ─────────────────────────────────────────────────────────────────────
# Layer 1 — recogniser
# ─────────────────────────────────────────────────────────────────────
class TestShellCommandRecogniser(unittest.TestCase):
    def test_json_envelope_with_empty_files(self):
        self.assertTrue(_handoff_brief_is_shell_command(
            '{"command": "pip install twilio", "files": []}'
        ))
        # Spacing variations
        self.assertTrue(_handoff_brief_is_shell_command(
            '{"command":"npm install lodash","files":[]}'
        ))

    def test_raw_shell_commands(self):
        for cmd in [
            "Run `pip install twilio` in the container.",
            "Install the Twilio Python SDK by running pip install twilio.",
            "npm install lodash",
            "yarn add stripe",
            "apt-get install ffmpeg",
            "brew install redis",
            "sudo chmod +x script.sh",
            "docker pull mongo:7",
            "kubectl apply -f deploy.yaml",
            "python -m pip install requests",
            "rm -rf node_modules",
            "git clone https://github.com/x/y.git",
        ]:
            self.assertTrue(
                _handoff_brief_is_shell_command(cmd),
                f"missed shell command: {cmd!r}",
            )

    def test_legitimate_file_edits_pass(self):
        # These describe actual file-edit work and MUST NOT trip the
        # shell-command guard.
        for brief in [
            "Add a /api/health route to backend/routes/health.py that returns {'ok': True}.",
            "Update README.md with deployment instructions.",
            'Edit "package.json" to bump react to 19.0.0 (this is a file edit).',
            "Add twilio to requirements.txt and wire SMS_FROM env into routers/sms.py.",
        ]:
            self.assertFalse(
                _handoff_brief_is_shell_command(brief),
                f"file-edit brief wrongly flagged as shell: {brief!r}",
            )

    def test_empty_and_none_safe(self):
        self.assertFalse(_handoff_brief_is_shell_command(""))
        self.assertFalse(_handoff_brief_is_shell_command(None))


# ─────────────────────────────────────────────────────────────────────
# Layer 2 — ship-shortcut guard
# ─────────────────────────────────────────────────────────────────────
def _make_body(prompt: str, session_id: str = "s1"):
    return MagicMock(
        prompt=prompt,
        session_id=session_id,
        project_id="p_test",
        maxx_mode=None,
    )


def _stub_db_with_handoff_message(handoff_body: str):
    """Return a MagicMock db that exposes one chat_session whose last
    assistant message contains an aurem-handoff fence with the given body."""
    msg = (
        "Sure — here's the plan:\n\n"
        "```aurem-handoff\n"
        f"{handoff_body}\n"
        "```"
    )
    sess = {
        "session_id": "s1",
        "user_id": "u1",
        "messages": [
            {"role": "user", "content": "i saw twilio issues, debug"},
            {"role": "assistant", "content": msg},
        ],
    }
    db = MagicMock()
    db.chat_sessions = MagicMock()
    db.chat_sessions.find_one = AsyncMock(return_value=sess)
    return db


class TestShipShortcutRefusesShellHandoff(unittest.TestCase):
    """Iter 212m-26: _maybe_ship_shortcut() and the entire auto-ship
    keyword path were deleted. Manual click is the only ship route now.
    The two tests that used to live here are gone with the feature."""
    def test_no_longer_applicable(self):
        # This class is intentionally a no-op stub so the test report
        # has a visible record of the removal.
        from routers import chat as chat_mod
        self.assertFalse(hasattr(chat_mod, "_maybe_ship_shortcut"))
        self.assertFalse(hasattr(chat_mod, "_looks_like_ship_confirmation"))


# ─────────────────────────────────────────────────────────────────────
# Layer 3 — broader follow-up guard
# ─────────────────────────────────────────────────────────────────────
class TestFollowupGuard(unittest.IsolatedAsyncioTestCase):
    async def _run_with_handoff(self, prompt: str, handoff: str):
        body = _make_body(prompt)
        from routers import chat as chat_mod
        db = _stub_db_with_handoff_message(handoff)
        orig = chat_mod.get_db
        chat_mod.get_db = lambda: db
        try:
            return await _maybe_guard_shell_handoff_followup(
                body=body, user_id="u1",
            )
        finally:
            chat_mod.get_db = orig

    async def test_short_followup_after_shell_handoff_intercepted(self):
        # All of these are the patterns the user actually typed that
        # caused the 365 s hang.
        SHELL = '{"command": "pip install twilio", "files": []}'
        for p in [
            "install",
            "do it",
            "do it fix the issue properly",
            "now install it",
            "make it work",
            "fix it",
        ]:
            r = await self._run_with_handoff(p, SHELL)
            self.assertIsNotNone(r, f"guard missed: {p!r}")
            self.assertIn("shell command", r.lower())
            self.assertIn("requirements.txt", r)

    async def test_long_substantive_followup_falls_through(self):
        # > 60 chars or contains a file path → user is adding new info
        # and we should NOT intercept; the orchestrator can run.
        SHELL = '{"command": "pip install twilio", "files": []}'
        for p in [
            "actually no, instead add twilio to backend/requirements.txt please",  # 67 chars
            "wait — instead edit src/sms.py to use TwilioRestClient",  # has path
            "backslash\\path\\thing",  # contains \\
        ]:
            r = await self._run_with_handoff(p, SHELL)
            self.assertIsNone(r, f"guard wrongly fired: {p!r}")

    async def test_followup_after_legit_handoff_falls_through(self):
        # If the prior handoff was a real file-edit task, normal short
        # confirmations must reach the ship-shortcut path.
        LEGIT = "Edit backend/foo.py to add a /api/health route."
        for p in ["ship", "do it", "go ahead"]:
            r = await self._run_with_handoff(p, LEGIT)
            self.assertIsNone(r, f"guard wrongly fired on legit handoff: {p!r}")

    async def test_no_prior_handoff_no_guard(self):
        # First chat turn — no assistant message at all.
        body = _make_body("install")
        from routers import chat as chat_mod
        empty_db = MagicMock()
        empty_db.chat_sessions = MagicMock()
        empty_db.chat_sessions.find_one = AsyncMock(return_value={"messages": []})
        orig = chat_mod.get_db
        chat_mod.get_db = lambda: empty_db
        try:
            r = await _maybe_guard_shell_handoff_followup(
                body=body, user_id="u1",
            )
            self.assertIsNone(r)
        finally:
            chat_mod.get_db = orig


if __name__ == "__main__":
    unittest.main()
