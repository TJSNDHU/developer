"""Iter 331 · DELETE GATE — permanence locks.

The gate artifacts must never silently disappear (the whole point is
that this bug class — deleting lazy-imported files — happened 3×)."""
import os
from pathlib import Path

ROOT = Path("/app")


def test_check_script_exists_and_executable():
    p = ROOT / "scripts" / "check-safe-to-delete.sh"
    assert p.exists()
    assert os.access(p, os.X_OK), "script must stay executable"
    src = p.read_text(encoding="utf-8")
    for section in ("LAZY / dynamic imports", "String references", "VERDICT"):
        assert section in src


def test_gate_doc_exists_with_template_and_verdicts():
    doc = (ROOT / "docs" / "DELETE_GATE.md").read_text(encoding="utf-8")
    assert "Script output:" in doc                      # Layer 2 template
    assert "Quarantine" in doc or "quarantine" in doc   # Layer 3
    for f in ("tool_executor.py", "tools_bridge.py",
              "VisualFixtures.jsx", "LoopLiveFeedDemo.jsx"):
        assert f in doc, f"verdict record for {f} missing"


def test_ci_delete_gate_job_present():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "delete-gate:" in ci
    assert "check-safe-to-delete" in ci
    assert "docs/DELETE_GATE.md" in ci
