"""
test_strip_tool_calls.py — Iter 35: guard against the "raw tool_call JSON
leaks to user" bug reported on production.

When the orchestrator hits max_iters with the LLM still emitting tool
fences, the final streamed content used to contain the raw ```tool_call```
JSON blocks — they rendered as a code-fenced markdown chunk in the chat
UI and confused users into thinking AUREM was asking them to execute
the call. Now strip_tool_calls() removes them BEFORE streaming.
"""
from __future__ import annotations

import pytest

from services.tools_bridge import strip_tool_calls, extract_tool_calls


# Real fence the user saw on production (transcript shared by founder)
USER_VISIBLE_BUG = """I need to read the full pillar health router and \
check the actual router registration to diagnose the 14 red routers issue.

```tool_call
{"tool": "read_repo_file", "args": {"path": "backend/routers/pillars_health_router.py"}}
```

```tool_call
{"tool": "read_repo_file", "args": {"path": "backend/services/pillar_heartbeat_service.py"}}
```

```tool_call
{"tool": "search_repo", "args": {"pattern": "router.*=.*APIRouter", "path": "backend/routers", "ext": ".py"}}
```
"""


def test_strip_removes_all_tool_call_fences():
    cleaned = strip_tool_calls(USER_VISIBLE_BUG)
    assert "tool_call" not in cleaned
    assert "read_repo_file" not in cleaned
    assert "search_repo" not in cleaned
    # The prose preceding the fences must be kept
    assert "I need to read the full pillar health router" in cleaned
    assert "14 red routers" in cleaned


def test_strip_is_safe_on_clean_text():
    msg = "All clear. The bug was at line 47, fix shipped."
    assert strip_tool_calls(msg) == msg


def test_strip_handles_json_fence_alias():
    """The regex matches both ```tool_call``` and ```json``` because some
    weaker models emit them as plain json fences."""
    text = 'Result:\n\n```json\n{"tool": "x", "args": {}}\n```\n\nDone.'
    cleaned = strip_tool_calls(text)
    assert '"tool"' not in cleaned
    assert "Result:" in cleaned
    assert "Done." in cleaned


def test_extract_then_strip_pairs():
    """Sanity: anything extract_tool_calls finds, strip_tool_calls removes."""
    calls = extract_tool_calls(USER_VISIBLE_BUG)
    assert len(calls) == 3
    assert calls[0]["tool"] == "read_repo_file"
    cleaned = strip_tool_calls(USER_VISIBLE_BUG)
    # Re-extracting from the cleaned output must yield zero calls
    assert extract_tool_calls(cleaned) == []


def test_strip_collapses_excessive_blank_lines():
    """When 3 fences sit back-to-back-to-back, removing them used to
    leave a wall of blank lines. We collapse to max 2 blanks."""
    text = "Top.\n\n```tool_call\n{\"tool\":\"a\",\"args\":{}}\n```\n\n```tool_call\n{\"tool\":\"b\",\"args\":{}}\n```\n\nBottom."
    cleaned = strip_tool_calls(text)
    # No 3-newline runs left
    assert "\n\n\n" not in cleaned
    assert "Top." in cleaned and "Bottom." in cleaned


def test_strip_on_empty_or_none():
    assert strip_tool_calls("") == ""
    assert strip_tool_calls(None) is None
