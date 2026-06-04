"""Iter 74 follow-up: TaskManagementPanel wiring lock."""
import os


def _read(rel: str) -> str:
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


def test_task_management_panel_exists():
    js = _read("frontend/src/components/TaskManagementPanel.jsx")
    assert "parseChecklist" in js
    assert "hasChecklist" in js
    assert "task-management-panel" in js
    # Three checklist states
    for state in ('"done"', '"active"', '"pending"'):
        assert state in js


def test_task_management_panel_wired_into_message_bubble():
    js = _read("frontend/src/components/MessageBubble.jsx")
    assert "TaskManagementPanel" in js
    assert "hasChecklist" in js
    # Renders only on assistant messages with checklist
    assert "m.role === \"assistant\" && hasChecklist(m.content)" in js
