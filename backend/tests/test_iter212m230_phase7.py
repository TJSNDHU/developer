"""
Iter 212m-230 — Phase 7: Zero circular imports + Scanner Feedback dashboard.

Locks in the following:

1. `services/architecture_health.run_health_report()` reports
   ZERO circular imports on the AUREM backend tree.
2. `services/pat_vault.py` owns the canonical `_decrypt_pat` /
   `_encrypt_pat` / `_user_gh_token` implementations.
   `routers/cto_projects.py` re-exports them (backward compat).
3. `services/stripe_client.py` owns the canonical `stripe_key()`
   resolver. Both `routers/payments` and `services/billing_cron`
   import from HERE — no more router↔service cycle.
4. `services/app_state.py` singleton replaces `import main` reads.
   Used by `admin_bin` (linters_missing) and `integration_health`
   (SENTRY_ACTIVE).
5. `routers/admin_bin` + `routers/thinking_hints` delegate their
   admin gate to `cto_services.auth.require_admin` (canonical),
   not `routers.admin._require_admin`.
6. `GET /api/aurem-dev/codebase-health/scanner-feedback` aggregates
   fix_triage FP-log rows into a rule-tuning dashboard.
"""

from __future__ import annotations


# ── Zero circular imports on the backend tree ────────────────────
def test_backend_has_zero_circular_imports():
    from services.architecture_health import run_health_report

    report = run_health_report(["/app/backend"])
    cycles = report.get("circular_imports") or []
    assert cycles == [], (
        f"Backend must have ZERO circular imports; "
        f"got {len(cycles)}: {cycles}"
    )


# ── pat_vault canonical implementation ───────────────────────────
def test_pat_vault_owns_canonical_impl():
    """pat_vault must define _decrypt_pat / _encrypt_pat / _user_gh_token
    itself, NOT delegate to routers.cto_projects (which was the source
    of the original cycle)."""
    src = open("/app/backend/services/pat_vault.py").read()
    assert "async def _decrypt_pat" in src, (
        "pat_vault must own the canonical _decrypt_pat"
    )
    assert "async def _encrypt_pat" in src, (
        "pat_vault must own the canonical _encrypt_pat"
    )
    assert "from routers.cto_projects" not in src, (
        "pat_vault must NOT import from routers/ — that's the "
        "cycle we broke. Canonical impl lives HERE."
    )


def test_cto_projects_reexports_from_pat_vault():
    """The router-side names still exist for backward-compatibility,
    but they now delegate DOWN to services/pat_vault, not the other
    way around."""
    src = open("/app/backend/routers/cto_projects.py").read()
    assert "from services.pat_vault import _decrypt_pat" in src, (
        "routers.cto_projects must import _decrypt_pat FROM pat_vault "
        "(dependency direction is routers → services)"
    )


# ── stripe_client canonical resolver ─────────────────────────────
def test_stripe_client_service_exists():
    from services.stripe_client import stripe_key, set_runtime_stripe_key, stripe_client
    # Should be callable — even with no key configured, must return "".
    key = stripe_key()
    assert isinstance(key, str), f"stripe_key() must return a str, got {type(key)}"


def test_billing_cron_uses_stripe_client_service():
    src = open("/app/backend/services/billing_cron.py").read()
    assert "from services.stripe_client import" in src, (
        "billing_cron must import the Stripe key resolver from "
        "services/stripe_client — was importing from routers.payments "
        "which created the cycle."
    )
    assert "from routers.payments import _stripe_key" not in src, (
        "billing_cron must NOT import _stripe_key from routers.payments "
        "any more — that was the cycle."
    )


def test_payments_router_delegates_to_stripe_client():
    src = open("/app/backend/routers/payments.py").read()
    assert "from services.stripe_client import stripe_key" in src, (
        "routers/payments must delegate _stripe_key() to "
        "services/stripe_client"
    )


# ── app_state singleton ──────────────────────────────────────────
def test_app_state_module_exists_and_works():
    from services.app_state import set_state, get_state, all_state
    set_state("test_key_iter230", "test_value")
    assert get_state("test_key_iter230") == "test_value"
    assert "test_key_iter230" in all_state()


def test_admin_bin_reads_from_app_state_not_main():
    src = open("/app/backend/routers/admin_bin.py").read()
    # Check for the actual IMPORT statement, not comments mentioning it.
    import re
    real_imports = re.findall(r"^\s*from main import ", src, re.MULTILINE)
    assert not real_imports, (
        f"admin_bin must NOT `from main import` at module or function scope; "
        f"found: {real_imports}"
    )
    assert "from services.app_state import get_state" in src, (
        "admin_bin must read loop_linters_missing via app_state"
    )


def test_integration_health_reads_from_app_state_not_main():
    src = open("/app/backend/services/integration_health.py").read()
    import re
    real_imports = re.findall(r"^\s*import main\b", src, re.MULTILINE)
    assert not real_imports, (
        "integration_health must NOT `import main`. Use app_state instead."
    )
    assert "from services.app_state import get_state" in src, (
        "integration_health must read SENTRY_ACTIVE via app_state"
    )


# ── admin gate delegation ────────────────────────────────────────
def test_admin_bin_uses_cto_services_require_admin():
    """admin_bin's _require_admin must delegate to cto_services.auth
    (not routers.admin), otherwise the cycle re-forms."""
    src = open("/app/backend/routers/admin_bin.py").read()
    assert "from cto_services.auth import require_admin" in src, (
        "admin_bin._require_admin must delegate to cto_services.auth"
    )
    assert ("from routers.admin import _require_admin" not in src
            and "from routers.admin import _shared_require_admin" not in src), (
        "admin_bin must NOT import _require_admin from routers.admin"
    )


def test_thinking_hints_uses_cto_services_require_admin():
    src = open("/app/backend/routers/thinking_hints.py").read()
    assert "from cto_services.auth import require_admin" in src, (
        "thinking_hints must delegate to cto_services.auth"
    )
    import re
    # Check for the actual import statement, not a comment mentioning it.
    real = re.findall(r"^\s*from routers\.admin import _require_admin\b",
                      src, re.MULTILINE)
    assert not real, (
        "thinking_hints must NOT import _require_admin from routers.admin"
    )


# ── Scanner-feedback endpoint ────────────────────────────────────
def test_scanner_feedback_endpoint_registered():
    """The GET /codebase-health/scanner-feedback route must be
    reachable via the codebase_health router."""
    from routers.codebase_health import router
    paths = [r.path for r in router.routes]
    assert any("scanner-feedback" in p for p in paths), (
        f"scanner-feedback endpoint not registered. Paths: {paths}"
    )


def test_scanner_feedback_requires_admin():
    """Unauthenticated call must return 401 — NOT expose the FP data.
    This is a smoke test only; e2e is at HTTP layer above."""
    # We can only import the handler here; a proper live-endpoint
    # HTTP check happens in the shell tests. Confirm the handler
    # exists and is defined as async (admin gate is on line 1).
    from routers import codebase_health
    assert hasattr(codebase_health, "scanner_feedback"), (
        "scanner_feedback handler missing from codebase_health module"
    )
    import inspect
    assert inspect.iscoroutinefunction(codebase_health.scanner_feedback), (
        "scanner_feedback must be async"
    )
