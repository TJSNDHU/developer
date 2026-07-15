"""
Iter 212m-225 — Architecture boundary hardening.

Locks:
1. `services/scanner_utils.py` exists and hosts `is_scanner_rule_file`
   (moved out of routers/ so services can import it without violating
   the router→service dependency direction).
2. `services/pat_vault.py` exists as the canonical PAT accessor shim.
3. `# arch: allow-http` file-level marker exempts routers with a
   legitimate reason for direct httpx use (OAuth callbacks, MCP,
   deploy provider bridges, admin probes).
4. `# arch: allow-router-import` inline marker exempts intentional
   service→router imports (e.g. `services/billing_cron` calling into
   the payments router's Stripe helper, `services/pat_vault` shim).
5. The `architecture_health` scanner honours both markers.
6. `shared/` tree is exempt from `http-call-outside-services` — those
   are the marketing agents, not user-facing code.

Regression: any boundary violation appearing here means a caller
newly introduced an unmarked router import from a service, OR a raw
httpx call sneaked into a router without the file marker.
"""

from __future__ import annotations


def test_scanner_utils_hosts_is_scanner_rule_file():
    """The helper MUST live in services/ so both routers/ and other
    services/ can import it without a boundary violation."""
    from services.scanner_utils import is_scanner_rule_file
    assert is_scanner_rule_file("services/bug_hunt_rules.py") is True
    assert is_scanner_rule_file("services/loop_engine.py")    is False


def test_pat_vault_delegates_to_router():
    """The shim MUST expose `decrypt_pat` and `get_user_gh_token`."""
    import services.pat_vault as v
    assert hasattr(v, "decrypt_pat")
    assert hasattr(v, "get_user_gh_token")


def test_no_boundary_violations_after_hardening():
    """The full-repo run must be clean.  This is the guard that
    prevents any future PR from silently reintroducing a router
    import from a service, or a raw httpx call in a router that
    doesn't self-document via the `# arch: allow-http` marker."""
    from services.architecture_health import run_health_report
    report = run_health_report(["/app/backend"])
    violations = report.get("boundary_violations", [])
    # Group by rule for a helpful assertion message if this ever fails.
    if violations:
        grouped: dict[str, list[str]] = {}
        for v in violations:
            grouped.setdefault(v.get("rule", "?"), []).append(
                f"{v.get('file')}  ({v.get('detail','')[:60]})"
            )
        msg = "New architecture boundary violations:\n" + "\n".join(
            f"  [{r}] × {len(files)}\n    " + "\n    ".join(files)
            for r, files in grouped.items()
        )
        raise AssertionError(msg)


def test_allow_http_marker_recognised_anywhere_in_file():
    """The marker used to be capped at the first 2 KB — long docstrings
    (fix_pipeline.py is > 2 KB) got missed. Confirm it's now found
    anywhere in the file body."""
    src = open("/app/backend/services/architecture_health.py").read()
    # The scanner should NOT slice src to a small prefix any more.
    assert "src[:2048]" not in src.split("_ALLOW_MARKER_RE")[-1], (
        "architecture_health scanner still caps the allow-http marker "
        "search at the file head — long docstrings will hide it."
    )


def test_allow_router_import_marker_supported():
    """`_scan_boundaries` must honour a same-line or previous-line
    `# arch: allow-router-import` marker for intentional shims
    (services/pat_vault.py etc.)."""
    src = open("/app/backend/services/architecture_health.py").read()
    assert "# arch: allow-router-import" in src, (
        "opt-out marker string missing from _scan_boundaries — "
        "the shim exemption is no longer honoured."
    )
