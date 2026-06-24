"""Iter 212m-21 — Ask Advisor primary LLM swap (Claude/Aurem.live → GLM-5.2).

Before this iter:
  • Founders calling Ask Advisor (agent="ora") were routed to
    aurem.live's hosted ORA model via services.ora_client.call_ora.
  • Non-founders silently downgraded to the orchestrator (which since
    Iter 212m-18 uses Swift→GLM-5.2 already).
  • The /chat/ora/draft-support-email endpoint used
    deepseek/deepseek-chat for the email draft.

After this iter:
  • Every Ask Advisor LLM call routes through GLM-5.2 (z-ai/glm-5.2)
    via OpenRouter, using the EXISTING _call_glm() function from
    services/llm.py (Iter 212m-18). No new LLM wrappers.
  • The aurem.live upstream branch is removed; its graceful fallback
    to the orchestrator path stays as a safety net.
  • Step events still stream — _step is now defined at the top of
    _worker so the agent="ora" branch can emit phase frames before
    the orchestrator block does.
"""
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


# ── Primary swap: agent="ora" now calls _call_glm ──────────────────


def test_ora_branch_calls_call_glm_not_call_ora():
    src = (BACKEND / "routers" / "chat.py").read_text(encoding="utf-8")
    # The old aurem.live upstream call is gone.
    assert "from services.ora_client import call_ora" not in src, (
        "Ask Advisor must no longer import call_ora — it now routes "
        "through _call_glm via OpenRouter (Iter 212m-21)"
    )
    # And the new GLM call is wired into the agent='ora' branch.
    assert "from services.llm import _call_glm, _GLM_MODEL" in src
    # The body of the branch must await _call_glm with the project-
    # context system prompt + the user's prompt.
    assert "await _call_glm(" in src


def test_ora_branch_uses_glm_model_constant():
    src = (BACKEND / "routers" / "chat.py").read_text(encoding="utf-8")
    # The result dict for the ora branch must publish "glm-5.2" as the
    # provider so the frontend pill, telemetry, and floating progress
    # card all show the right model name.
    # Locate the ora branch by its activity label.
    idx = src.find('"asking GLM-5.2…"')
    assert idx >= 0, "ora→GLM branch missing — activity label gone"
    body = src[idx:idx + 2000]
    assert '"provider":        "glm-5.2"' in body
    assert '"model":           _GLM_MODEL' in body
    assert '"fallback_chain":  ["glm-5.2"]' in body
    assert '"mode":            "ora"' in body


def test_ora_branch_uses_ora_panel_tone_system_prompt():
    src = (BACKEND / "routers" / "chat.py").read_text(encoding="utf-8")
    idx = src.find('"asking GLM-5.2…"')
    body = src[idx:idx + 2000]
    # The Ask Advisor persona must still apply on top of repo/brain ctx.
    assert "ORA_PANEL_TONE" in body
    assert "extra_sys" in body


def test_ora_branch_falls_back_to_orchestrator_on_glm_error():
    src = (BACKEND / "routers" / "chat.py").read_text(encoding="utf-8")
    idx = src.find('"asking GLM-5.2…"')
    body = src[idx:idx + 3000]
    # If GLM raises, log + fall through to the orchestrator path —
    # the user never sees a blank reply.
    assert "except Exception as glm_err" in body
    assert "Fall through to the AUREM/orchestrator path below" in body


def test_step_helper_defined_at_top_of_worker():
    """The Iter 212m-19 _step callback must be defined at the top of
    _worker, BEFORE the agent="ora" branch runs, otherwise Python
    raises UnboundLocalError and silently falls through."""
    src = (BACKEND / "routers" / "chat.py").read_text(encoding="utf-8")
    # Find the start of _worker and the first occurrence of _step
    # definition AND the first call inside the ora branch.
    worker_idx     = src.find("async def _worker():")
    step_def_idx   = src.find("def _step(", worker_idx)
    ora_label_idx  = src.find('"asking GLM-5.2…"', worker_idx)
    assert worker_idx >= 0
    assert step_def_idx > worker_idx
    # `_step` MUST be defined BEFORE the agent="ora" block fires it.
    assert step_def_idx < ora_label_idx, (
        "_step must be defined ABOVE the agent='ora' branch — "
        "otherwise calling it from inside that branch raises "
        "UnboundLocalError and the GLM call is silently skipped"
    )


# ── Secondary swap: /draft-support-email uses GLM ─────────────────


def test_draft_support_email_uses_glm_model():
    src = (BACKEND / "routers" / "chat.py").read_text(encoding="utf-8")
    # The DeepSeek model literal must be gone from the draft-support-
    # email call_openrouter_model() invocation. Scope the search to the
    # actual `call_openrouter_model(...)` block so the regex doesn't
    # collide with the email PROMPT text (which mentions "chat" /
    # "deepseek" as natural language).
    idx = src.find("email_body = await call_openrouter_model(")
    end = src.find(")", idx)
    block = src[idx:end + 1]
    assert "deepseek/deepseek-chat" not in block, (
        "draft-support-email's call_openrouter_model(...) must no "
        "longer reference deepseek-chat — it should pass _GLM_MODEL"
    )
    assert "_GLM_MODEL" in block
    # And the helper must be imported at the call site.
    around = src[max(0, idx - 800):idx]
    assert "from services.llm import _GLM_MODEL" in around


# ── Non-founder path was already GLM (Iter 212m-18) ───────────────


def test_swift_review_mode_still_routes_to_glm():
    """The non-founder Ask Advisor path silently downgrades agent='ora'
    to agent='auto' and the orchestrator routes Swift mode → GLM. The
    pin from iter 212m-18 must still hold."""
    src = (BACKEND / "services" / "llm.py").read_text(encoding="utf-8")
    assert '_GLM_MODEL = os.getenv("GLM_MODEL", "z-ai/glm-5.2")' in src
    assert 'if rm == "swift":' in src
    assert 'provider":       "glm-5.2"' in src or \
           '"provider":       "glm-5.2"' in src


# ── Tier downgrade path still works ───────────────────────────────


def test_non_founder_agent_ora_silently_downgrades_to_auto():
    src = (BACKEND / "routers" / "chat.py").read_text(encoding="utf-8")
    # Iter 205 — for non-founders we don't 403, we downgrade. That
    # logic must stay so the rest of the world (paid + free) lands
    # on the orchestrator (which itself routes to GLM via swift mode).
    assert "if not is_founder_email(user.get(\"email\")):" in src
    assert 'body.agent = "auto"' in src
