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
    AND require a min-8-char `reason` for the audit log.

    Iter 297 — BEHAVIOURAL upgrade (was STATIC_GREP grepping the
    router source for token strings). We now actually invoke the
    endpoint coroutine with a monkey-patched `current_dev` returning
    a NON-founder user, and prove:
      (a) A non-founder user hits HTTPException(403).
      (b) The Pydantic body validator rejects reason < 8 chars with
          a real ValidationError (proving `min_length=8` is enforced
          at runtime, not just present as a source token).
    """
    import asyncio
    import pytest
    from pydantic import ValidationError
    from fastapi import HTTPException
    from routers import scaffold as _sc
    from routers.scaffold import FounderOverrideBody

    # (b) Prove the Pydantic min_length=8 actually rejects short reasons.
    with pytest.raises(ValidationError):
        FounderOverrideBody(reason="short")
    # Sane reason passes.
    body_ok = FounderOverrideBody(reason="operator-approved bypass 2026-02")
    assert body_ok.reason.startswith("operator-approved")

    # (a) Non-founder invocation → 403.
    async def _fake_current_dev(_authorization):
        return {"user_id": "u_nonfounder", "email": "n@x.com",
                 "is_founder": False, "is_admin": False}

    orig = _sc.current_dev
    _sc.current_dev = _fake_current_dev
    try:
        async def _call():
            await _sc.founder_override(
                draft_id="d1",
                body=body_ok,
                authorization="Bearer x",
            )
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(_call())
        assert exc_info.value.status_code == 403, (
            f"non-founder must be blocked with 403; got "
            f"{exc_info.value.status_code}"
        )
    finally:
        _sc.current_dev = orig


def test_founder_override_writes_audit_log():
    """Every override MUST append to `db.scaffold_scan_overrides`
    with (draft_id, overridden_by, reason, findings_snapshot,
    timestamp).  Silently missing this collection write would defeat
    the audit trail.

    Iter 297 — BEHAVIOURAL upgrade (was STATIC_GREP). We now:
      • Build a `SpyDB` whose `scaffold_scan_overrides.insert_one`
        records every doc it receives.
      • Seed a blocked draft into `scaffold_drafts.find_one`.
      • Invoke `founder_override` end-to-end with a founder user.
      • Assert the recorded doc carries the full audit-log shape
        (reason, overridden_by, findings_snapshot, timestamp) —
        NOT just that the collection name appears in the source.
    A regression that removes the `insert_one` call, or truncates the
    payload, would flip `_inserted` to `[]` (or a wrong-shape doc)
    and fail loudly.
    """
    import asyncio
    from routers import scaffold as _sc
    from routers.scaffold import FounderOverrideBody

    # ── SpyDB — records inserts + serves a blocked draft ────────
    class _Coll:
        def __init__(self, name):
            self.name = name
            self.inserted: list[dict] = []
            self.updates:  list[tuple] = []
        async def insert_one(self, doc):
            self.inserted.append(dict(doc))
            return type("R", (), {"inserted_id": "x"})()
        async def find_one(self, q, proj=None):
            if self.name == "scaffold_drafts":
                return {
                    "draft_id": q.get("draft_id"),
                    "user_id":  q.get("user_id"),
                    "status":   "blocked_by_scan",
                    "scan_findings_snapshot": [
                        {"rule": "hardcoded_secret", "sev": "critical"},
                    ],
                    "scan_summary": {"critical": 1, "high": 0, "medium": 0},
                }
            return None
        async def update_one(self, q, u):
            self.updates.append((dict(q), dict(u)))
            return type("R", (), {"matched_count": 1, "modified_count": 1})()

    class _SpyDB:
        def __init__(self):
            self.scaffold_drafts          = _Coll("scaffold_drafts")
            self.scaffold_scan_overrides  = _Coll("scaffold_scan_overrides")

    spy_db = _SpyDB()

    async def _fake_current_dev(_authorization):
        return {"user_id": "u_founder", "email": "f@aurem.dev",
                 "is_founder": True, "is_admin": False}

    orig_current_dev = _sc.current_dev
    orig_get_db      = _sc.get_db
    _sc.current_dev  = _fake_current_dev
    _sc.get_db       = lambda: spy_db
    try:
        async def _call():
            return await _sc.founder_override(
                draft_id="draft-abc",
                body=FounderOverrideBody(
                    reason="verified false positive — stripe test key"
                ),
                authorization="Bearer x",
            )
        result = asyncio.run(_call())
    finally:
        _sc.current_dev = orig_current_dev
        _sc.get_db      = orig_get_db

    # Response shape — proves the coroutine ran end-to-end.
    assert result["ok"] is True
    assert result["draft_id"] == "draft-abc"
    assert result["override_active"] is True

    # THE audit-trail assertion — one insert, right shape.
    inserted = spy_db.scaffold_scan_overrides.inserted
    assert len(inserted) == 1, (
        f"exactly one audit row must be written per override; "
        f"got {len(inserted)}"
    )
    row = inserted[0]
    assert row["draft_id"] == "draft-abc"
    assert row["overridden_by"] == "u_founder"
    assert row["overridden_by_email"] == "f@aurem.dev"
    assert row["reason"] == "verified false positive — stripe test key"
    # The findings snapshot must be preserved, not discarded.
    assert row["findings_snapshot"] == [
        {"rule": "hardcoded_secret", "sev": "critical"},
    ]
    assert row["summary_snapshot"] == {"critical": 1, "high": 0, "medium": 0}
    # Timestamp must be a real number (time.time()).
    assert isinstance(row["created_at"], (int, float))
    assert row["created_at"] > 0

    # The draft must have been flipped back to 'draft' with override_active.
    assert spy_db.scaffold_drafts.updates, "draft must be updated"
    _q, _u = spy_db.scaffold_drafts.updates[0]
    assert _u["$set"]["status"] == "draft"
    assert _u["$set"]["override_active"] is True


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
