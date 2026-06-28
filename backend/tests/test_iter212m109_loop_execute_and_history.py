"""
test_iter212m109_loop_execute_and_history.py — Iter 212m-109

Locks 4 user-reported P0/P1 bugs:

BUG 1 — Loop EXECUTE now actually generates file content via LLM.
        Previously a synthetic loop emitting "Wrote {f}" without ever
        producing real code, so SHIP found nothing to commit and the
        user saw "Ship complete" with no real GitHub commit (3 reproductions).

BUG 2 — Chat history bumped from last 20 turns to last 100 turns. Loop
        runs emit 8-15 events each, so 20 truncated after 2-3 runs and
        earlier user messages vanished on reload.

BUG 3 — Console error badge no longer auto-sends. Now shows a confirm
        dialog; cancel copies the payload to clipboard instead.

BUG 4 — Send button is type="button" with explicit preventDefault +
        stopPropagation, so the form-submit + onClick race is gone.
"""
from pathlib import Path


def _read_fe(rel: str) -> str:
    return Path(f"/app/frontend/src/{rel}").read_text(encoding="utf-8")


def _read_be(rel: str) -> str:
    return Path(f"/app/backend/{rel}").read_text(encoding="utf-8")


def test_loop_execute_generates_real_file_content_via_llm():
    src = _read_be("services/loop_engine.py")
    # The execute phase must import and call the new generate_files()
    # helper that actually invokes the LLM.
    assert "from services.loop_execute import generate_files" in src
    assert "await generate_files(" in src
    # Failure paths must NOT silently succeed — they fail the loop.
    assert "_fail(\"execute\"" in src
    # The submitted_files must be populated from the generated output
    # so SHIP has something to commit.
    assert 'self.context["submitted_files"] = generated' in src


def test_loop_execute_helper_module_exists():
    src = _read_be("services/loop_execute.py")
    # The new module must be importable + define generate_files()
    assert "async def generate_files(" in src
    # Must use the same LLM helper the verify/self-heal flow uses.
    assert "from services.llm import call_llm_with_meta" in src
    # Must fetch current file content from GitHub via the existing writer.
    assert "from services.github_api_writer import fetch_file" in src
    # Verbose logging at every step so prod hangs can be diagnosed.
    assert "logger.info" in src and "[execute]" in src


def test_loop_ship_has_attempt_and_result_logs():
    src = _read_be("services/loop_engine.py")
    # Per user instruction: SHIP ATTEMPT + SHIP RESULT prints/logs.
    assert "SHIP ATTEMPT" in src
    assert "SHIP RESULT" in src


def test_chat_history_returns_last_100_not_20():
    src = _read_be("routers/chat.py")
    # The actual line is `turns = ((doc or {}).get("turns") or [])[-100:]`
    # so we assert the slice token directly, not "turns[-100:]".
    assert "[-100:]" in src
    # The old slice must be gone so a future merge can't silently
    # regress the limit.
    assert "[-20:]" not in src


def test_console_error_badge_requires_confirmation():
    src = _read_fe("components/ChatPanel.jsx")
    # The F12 badge must NOT auto-fire form.requestSubmit() without
    # user consent. Now: window.confirm() gate + clipboard fallback.
    assert "window.confirm(" in src
    assert "Send the captured F12 errors to ORA for analysis" in src
    # Clipboard fallback on cancel.
    assert "navigator.clipboard?.writeText(JSON.stringify(payload" in src


def test_send_button_is_type_button_with_explicit_handlers():
    """BUG 4 — kill the form-submit + onClick race by making the send
    button a standalone type=\"button\" and stopping event propagation."""
    src = _read_fe("components/ChatPanel.jsx")
    # The send button must be type="button" (no form-submit collision)
    assert 'type="button" data-testid="chat-send"' in src
    # Both preventDefault and stopPropagation must be called.
    assert "e.preventDefault();" in src
    assert "e.stopPropagation();" in src
