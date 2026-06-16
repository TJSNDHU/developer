"""Iter 168 — phase classifier unit tests for the live task popup."""
import pytest

from routers.cto_projects import _classify_phase


@pytest.mark.parametrize("step,expected", [
    ("📡 Reading owner/repo@main via API…",        "phase_read"),
    ("📄 read services/llm.py",                    "phase_read"),
    ("Cloning owner/repo@main…",                   "phase_read"),
    ("✅ Cloned",                                   "phase_read"),
    ("🧠 DeepSeek thinking…",                      "phase_think"),
    ("Plan: 3 files",                              "phase_think"),
    ("🧠 injected project memory",                 "phase_read"),  # 🗂 / 🧠 ambiguity — read wins (injection = reading context)
    ("✏️ 4 files to update",                       "phase_write"),
    ("💾 services/llm.py",                         "phase_write"),
    ("Writing files…",                             "phase_write"),
    ("Running linter…",                            "phase_write"),  # "linter" classed as write/validate stage
    ("🛡️ Vanguard verify agent reviewing patch…", "phase_verify"),
    ("Verifying 4 file(s) on remote",              "phase_verify"),
    ("🚀 pushed — abc123",                         "phase_commit"),
    ("Committing to GitHub…",                      "phase_commit"),
    ("plain log line with no markers",             None),
])
def test_classify(step, expected):
    assert _classify_phase(step) == expected


def test_empty_string():
    assert _classify_phase("") is None
    assert _classify_phase(None) is None
