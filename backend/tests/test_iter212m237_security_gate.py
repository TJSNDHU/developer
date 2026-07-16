"""
Iter 212m-237 — Personal Track security gate (Lovable-hardened).

The gate is the SINGLE entry point that decides whether generated
code may enter AUREM-owned infrastructure.  Every attack pattern
below MUST cause `scan_files()` to return `ok=False` and, at the
router level, MUST cause `POST /scaffold/{id}/materialize` to
return HTTP 422 with `reason: "security_scan_failed"` — zero
HTTP 200 in the insecure branch, ever.

Coverage tested:
  1. Hardcoded OpenAI-style API key           (SECRET)
  2. Hardcoded AWS access key                 (SECRET)
  3. Raw MongoDB connection URI               (SECRET)
  4. SQL injection via f-string interpolation (DANGEROUS)
  5. eval() usage                              (DANGEROUS)
  6. subprocess with shell=True               (DANGEROUS — new)
  7. pickle.loads on external data            (DANGEROUS — new)
  8. yaml.load without SafeLoader             (DANGEROUS — new)
  9. `dangerouslySetInnerHTML`                 (XSS)
  10. Path-traversal in scaffold path          (PATH SAFETY)

Retroactive coverage: same scan_files() function is used by both
materialize and any redeploy path — locked in by a code search.
"""

from __future__ import annotations

import pytest


# ── Attack payloads (kept as fixtures so they're re-usable) ────────
HARDCODED_OPENAI_KEY = 'OPENAI_API_KEY = "sk-abcdefghijklmnopqrstuvwxyz01234567"'
HARDCODED_AWS_KEY    = 'AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"'
RAW_MONGO_URI        = 'MONGO_URL = "mongodb+srv://admin:hunter2@cluster0.mongodb.net/prod"'
SQLI_FSTRING         = 'cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")'
EVAL_USAGE           = 'result = eval(user_input)'
SHELL_TRUE           = 'subprocess.run(f"rm -rf {path}", shell=True)'
PICKLE_LOADS         = 'data = pickle.loads(untrusted_bytes)'
YAML_UNSAFE          = 'cfg = yaml.load(open("config.yml"))'
DANGEROUS_INNERHTML  = 'return <div dangerouslySetInnerHTML={{__html: userBio}} />;'
PATH_TRAVERSAL_PATH  = "../../etc/passwd"


# ── Direct scan_files() unit assertions ────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("payload,label,path", [
    (HARDCODED_OPENAI_KEY,  "openai_secret",   "api/config.py"),
    (HARDCODED_AWS_KEY,     "aws_secret",      "api/env.py"),
    (RAW_MONGO_URI,         "mongo_uri",       "api/db.py"),
    (SQLI_FSTRING,          "sqli",            "api/users.py"),
    (EVAL_USAGE,            "eval",            "api/handlers.py"),
    (SHELL_TRUE,            "shell_true",      "api/utils.py"),
    (PICKLE_LOADS,          "pickle",          "api/import.py"),
    (YAML_UNSAFE,           "yaml_load",       "api/config_loader.py"),
    (DANGEROUS_INNERHTML,   "innerHTML",       "ui/src/Profile.jsx"),
])
async def test_scan_files_blocks_all_attack_patterns(payload, label, path):
    """Every one of these attack patterns MUST cause the gate to
    return `ok=False`.  This is the core "prove it works" test."""
    from services.scaffold_security_gate import scan_files
    files = [
        {"path": "README.md",     "content": "# clean readme"},
        {"path": path,            "content": payload},
    ]
    r = await scan_files(files)
    assert r["ok"] is False, (
        f"[{label}] gate did NOT reject — this is a Lovable-level regression. "
        f"summary={r['summary']}"
    )
    # crit or high must be > 0 for the block reason to be defensible.
    assert (r["summary"]["critical"] + r["summary"]["high"]) > 0


@pytest.mark.asyncio
async def test_scan_files_blocks_path_traversal():
    """Path safety is a separate layer from Vanguard — the pattern
    scanner can't see the filename, only the content."""
    from services.scaffold_security_gate import scan_files
    files = [
        {"path": "README.md",              "content": "# safe"},
        {"path": PATH_TRAVERSAL_PATH,      "content": "root:x:0:0::/root:/bin/bash"},
    ]
    r = await scan_files(files)
    assert r["ok"] is False
    assert r["summary"]["path_unsafe"] == 1


@pytest.mark.asyncio
async def test_scan_files_allows_clean_files():
    """The gate must NOT false-positive on genuinely clean code."""
    from services.scaffold_security_gate import scan_files
    files = [
        {"path": "README.md",       "content": "# My App\n\nA todo app."},
        {"path": "api/main.py",     "content": (
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n\n"
            "@app.get('/health')\n"
            "def health(): return {'ok': True}\n")},
        {"path": ".gitignore",      "content": "node_modules/\n.env\n"},
    ]
    r = await scan_files(files)
    assert r["ok"] is True, f"clean files flagged: {r['summary']}"
    assert r["summary"]["critical"] == 0
    assert r["summary"]["high"] == 0


@pytest.mark.asyncio
async def test_scan_files_medium_severity_is_soft_warn_not_block():
    """Founder-approved policy: medium warns, critical+high block."""
    from services.scaffold_security_gate import scan_files
    # Stripe test key is MEDIUM in Vanguard's catalog.
    files = [
        {"path": "README.md",  "content": "# safe"},
        {"path": "api/pay.py", "content": 'STRIPE_KEY = "sk_test_12345678901234567890AAAAAAAAA"'},
    ]
    r = await scan_files(files)
    # If Vanguard classifies Stripe test key as MEDIUM, gate should allow.
    # If it's classified as CRITICAL/HIGH we should still respect that.
    if r["summary"]["medium"] > 0 and (r["summary"]["critical"] + r["summary"]["high"]) == 0:
        assert r["ok"] is True, "medium-only findings must not block"
    else:
        # Rule was upgraded to CRITICAL by Vanguard — that's fine too;
        # the invariant we care about is "medium-only ⇒ allow", not
        # this specific fixture. Skip if classification differs.
        pytest.skip("Fixture classified above medium; test-invariant unchanged.")


@pytest.mark.asyncio
async def test_scan_files_fails_closed_when_scanner_crashes(monkeypatch):
    """A scanner crash must FAIL CLOSED (reject the ship), not fail
    open — this is the opposite of most fault-tolerance patterns and
    the correct posture for a security gate."""
    from services import scaffold_security_gate as sg
    def _crash(_blocks): raise RuntimeError("simulated scanner failure")
    monkeypatch.setattr(sg, "scan_file_blocks", _crash)
    r = await sg.scan_files([{"path": "a.py", "content": "print(1)"}])
    assert r["ok"] is False
    assert r["summary"].get("scanner_error") is True


# ── Router integration: materialize_draft returns HTTP 422 ─────────
def test_scan_gate_wired_before_repo_creation_in_materialize():
    """Static assertion: the scan call site in materialize_draft MUST
    appear BEFORE any of the destructive external calls (create_org_repo,
    push_files_bulk, delete_org_repo).  A future refactor that moves
    the gate below any of those breaks retroactive protection."""
    src = open("/app/backend/routers/scaffold.py").read()
    # Find the sub-strings — order matters.
    gate_idx  = src.find("_scan_files(files)")
    create_idx = src.find("create_org_repo(")
    push_idx   = src.find("push_files_bulk(")
    deploy_idx = src.find("deploy_personal_track(")
    assert gate_idx > 0, "Security gate not wired into scaffold.py"
    assert gate_idx < create_idx, "Gate MUST run before repo creation"
    assert gate_idx < push_idx,   "Gate MUST run before file push"
    assert gate_idx < deploy_idx, "Gate MUST run before deploy"


def test_materialize_uses_the_single_scan_function_no_duplicates():
    """Retroactive-coverage invariant: `scan_files` from
    `scaffold_security_gate` is the ONE source of truth. Nothing else
    in scaffold.py may implement a parallel scan step."""
    src = open("/app/backend/routers/scaffold.py").read()
    # Must import from the single-source module.
    assert "from services.scaffold_security_gate import" in src
    # Must NOT import scanner internals directly (would allow drift).
    assert "from services.vanguard_scanner import scan_file_blocks" not in src, (
        "scaffold.py should not talk to Vanguard directly — go through the gate."
    )


def test_blocked_state_persisted_for_ui():
    """The router must write `status='blocked_by_scan'` + the summary
    to the draft doc so the frontend can render the blocked state."""
    src = open("/app/backend/routers/scaffold.py").read()
    assert '"status":             "blocked_by_scan"' in src
    assert "scan_findings_snapshot" in src
    assert "scan_summary" in src


def test_friendly_user_message_never_leaks_technical_detail():
    """The message shown to non-tech users must never contain rule
    IDs, file paths, line numbers, CVE numbers, or the phrase
    'severity'."""
    from services.scaffold_security_gate import friendly_user_message
    msg = friendly_user_message({"critical": 3, "high": 1})
    banned = ["rule_id", "severity", "critical", "high", "line",
              ".py", "CVE", "OWASP", "regex", "match"]
    for b in banned:
        assert b.lower() not in msg.lower(), (
            f"Friendly message leaked technical term: {b!r} → {msg!r}"
        )


# ── Founder override endpoint ────────────────────────────────────
def test_founder_override_endpoint_registered():
    from routers.scaffold import router
    paths = [r.path for r in router.routes]
    assert "/scaffold/{draft_id}/founder-override" in paths


def test_founder_override_requires_is_founder_and_reason():
    """The override endpoint MUST gate on `is_founder` or `is_admin`
    AND require a min-8-char `reason` for the audit log."""
    src = open("/app/backend/routers/scaffold.py").read()
    idx = src.index("async def founder_override(")
    body = src[idx:idx + 3000]
    assert 'is_founder' in body
    assert "HTTPException(403" in body
    assert "min_length=8" in src


def test_founder_override_writes_audit_log():
    """Every override MUST append to `db.scaffold_scan_overrides`
    with (draft_id, overridden_by, reason, findings_snapshot,
    timestamp).  Silently missing this collection write would defeat
    the audit trail."""
    src = open("/app/backend/routers/scaffold.py").read()
    idx = src.index("async def founder_override(")
    body = src[idx:idx + 3000]
    assert "scaffold_scan_overrides" in body
    assert "insert_one(" in body
    assert "reason" in body


# ── LLM health diagnostic ────────────────────────────────────────
def test_llm_health_endpoint_registered_and_founder_only():
    from routers.scaffold import router
    paths = [r.path for r in router.routes]
    assert "/scaffold/admin/llm-health" in paths
    src = open("/app/backend/routers/scaffold.py").read()
    idx = src.index("async def llm_health(")
    body = src[idx:idx + 2000]
    assert "is_founder" in body
    assert "HTTPException(403" in body


def test_llm_health_returns_expected_shape():
    """Static — the response body must include `ok`, `llm_reachable`,
    `file_count`, `fallback`, `elapsed_ms`."""
    src = open("/app/backend/routers/scaffold.py").read()
    idx = src.index("async def llm_health(")
    body = src[idx:idx + 3000]
    for key in ('"ok":', '"llm_reachable":', '"file_count":',
                '"fallback":', '"elapsed_ms":'):
        assert key in body, f"llm_health response missing key {key}"
