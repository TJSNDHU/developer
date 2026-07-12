"""Iter 212m-192 — Extractor must catch XML-fenced tool calls.

Reproduces the exact glm-5.2 emission observed on production during
an Ask Advisor turn:

    <tool_call>read_repo_file)("README.md")

This shape was invisible to the extractor while the stripper already
recognised it, so the tool never ran (`tool_calls_run: 0`) and the
user saw "cannot access repo" replies even with a healthy PAT and a
green sidebar dot. The 5th shape now covers XML fences in all three
sub-cases: JSON envelope, Python-style call, and the malformed shape
by scanning for a known tool name + first string literal.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.tools_bridge import extract_tool_calls  # noqa: E402


def test_glm52_malformed_xml_shape_extracts_read_repo_file():
    """The exact broken emission observed on prod must now resolve to
    a real `read_repo_file` call with `path=README.md`."""
    text = '<tool_call>read_repo_file)("README.md")'
    calls = extract_tool_calls(text)
    assert calls, "malformed XML tool_call must yield at least one call"
    assert calls[0]["tool"] == "read_repo_file"
    # Best-effort positional mapping — `read_repo_file` takes `path`.
    assert calls[0]["args"].get("path") == "README.md"


def test_xml_wrapped_json_envelope_is_extracted():
    """Well-formed XML with a JSON body — round-trips cleanly."""
    text = (
        '<tool_call>'
        '{"tool": "list_repo_files", "args": {"path": "backend/routers"}}'
        '</tool_call>'
    )
    calls = extract_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["tool"] == "list_repo_files"
    assert calls[0]["args"]["path"] == "backend/routers"


def test_xml_wrapped_python_call_is_extracted():
    """XML wrapper around a valid Python-style call also resolves."""
    text = '<function_call>search_repo(query="tool_calls_run")</function_call>'
    calls = extract_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["tool"] == "search_repo"
    assert calls[0]["args"]["query"] == "tool_calls_run"


def test_unknown_tool_name_in_xml_block_is_ignored():
    """A malformed XML block that mentions no known tool must NOT
    fabricate a call (previous regression: shape 5 nlp-extraction)."""
    text = "<tool_call>please help me understand this file</tool_call>"
    calls = extract_tool_calls(text)
    assert calls == []


def test_fenced_json_still_wins_when_present():
    """Preserve the primary parser path — the fenced-JSON shape must
    still take precedence over the new XML fallback."""
    text = (
        "```tool_call\n"
        '{"tool": "read_repo_file", "args": {"path": "a.py"}}\n'
        "```\n"
        "<tool_call>some noise</tool_call>"
    )
    calls = extract_tool_calls(text)
    assert len(calls) >= 1
    assert calls[0]["tool"] == "read_repo_file"
    assert calls[0]["args"]["path"] == "a.py"
