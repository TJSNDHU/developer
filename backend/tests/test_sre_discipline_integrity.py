"""
Continuous Quality System — SRE/DORA plumbing tests.

Verifies the ancillary discipline files (postmortems + MTTR log +
AGENTS.md sections) exist and stay well-formed. These are not
runtime behaviour tests; they're guardrails against silently
losing the discipline itself.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

ROOT       = Path("/app")
AGENTS_MD  = ROOT / "AGENTS.md"
MTTR_LOG   = ROOT / "memory" / "mttr_log.json"
POSTMORTEM_DIR = ROOT / "postmortems"


def test_agents_md_has_all_required_sections():
    """
    AGENTS.md must contain the 6 permanent-rule sections we agreed on.
    A silent removal of any of these breaks the discipline layer.
    """
    src = AGENTS_MD.read_text()
    required = [
        "Bug-fix discipline",
        "Touching existing untested code",
        "Verification standard",
        "Code-quality standard",
        "Graceful degradation",
        "Error Budget Policy",
        "Blameless Postmortem template",
        "MTTR tracking",
        "Release It! patterns checklist",
    ]
    missing = [s for s in required if s not in src]
    assert not missing, f"AGENTS.md missing required sections: {missing}"


def test_agents_md_uses_dora_terminology():
    """
    Any doc reference to reliability must say `Change Failure Rate`
    (DORA's exact term), not the older internal name `revert-rate`.
    This is what makes our numbers benchmarkable against public
    DORA tiers.
    """
    src = AGENTS_MD.read_text()
    assert "Change Failure Rate" in src, (
        "AGENTS.md must use DORA's 'Change Failure Rate' term "
        "so numbers are benchmarkable against public tiers."
    )
    # Note: `revert-rate` / `revert_rate` MAY appear inside the
    # rename-rule paragraph itself — that's expected.  What we
    # forbid is using it as the PRIMARY label.  Simple heuristic:
    # any dashboard/table row line must not use the old term.
    for line in src.splitlines():
        if line.lstrip().startswith("|") and "revert" in line.lower():
            assert "Change Failure Rate" in line, (
                f"Dashboard/table row uses old 'revert' label: {line}"
            )


def test_mttr_log_is_well_formed():
    """
    The MTTR log must be valid JSON with entries that have all
    required fields. Each entry's mttr_hours must match the
    reported→deployed delta within 1 minute.
    """
    from datetime import datetime

    data = json.loads(MTTR_LOG.read_text())
    assert "entries" in data, "mttr_log.json must have `entries` array"
    assert "_budget" in data, "mttr_log.json must declare _budget targets"
    budget = data["_budget"]
    assert budget["target_mttr_hours"] > 0
    assert 0 < budget["target_change_failure_rate"] < 1

    required_fields = {"iter", "slug", "reported_at", "deployed_at",
                       "mttr_hours", "postmortem", "class"}
    for e in data["entries"]:
        missing = required_fields - set(e.keys())
        assert not missing, f"MTTR entry {e.get('iter')} missing: {missing}"

        # Timestamp sanity + mttr_hours consistency (±1 min tolerance).
        r = datetime.fromisoformat(e["reported_at"].replace("Z", "+00:00"))
        d = datetime.fromisoformat(e["deployed_at"].replace("Z", "+00:00"))
        computed_h = (d - r).total_seconds() / 3600.0
        drift = abs(computed_h - e["mttr_hours"])
        assert drift < 1/60, (
            f"iter {e['iter']}: mttr_hours={e['mttr_hours']} but "
            f"reported→deployed delta = {computed_h:.3f}h "
            f"(drift {drift*3600:.1f}s > 60s tolerance)"
        )


def test_every_mttr_entry_links_to_a_real_postmortem():
    """
    An MTTR log entry without a postmortem is a broken link. Every
    entry must reference a file that actually exists on disk.
    """
    data = json.loads(MTTR_LOG.read_text())
    for e in data["entries"]:
        pm = ROOT / e["postmortem"]
        assert pm.is_file(), (
            f"iter {e['iter']}: postmortem file not found: {pm}"
        )
        # And the postmortem must reference the regression test by name.
        content = pm.read_text()
        assert "test_regression_iter" in content, (
            f"postmortem {pm.name} must link its regression test"
        )


def test_postmortem_template_sections_present():
    """
    Every postmortem must have the 6 canonical sections. Freeform
    docs drift; enforcing headings keeps them scannable.
    """
    required = ["What happened", "Root cause", "Fix",
                "Why our tests missed it", "Prevention", "MTTR"]
    for pm in sorted(POSTMORTEM_DIR.glob("iter*.md")):
        src = pm.read_text()
        missing = [h for h in required if f"## {h}" not in src]
        assert not missing, f"{pm.name} missing headings: {missing}"
