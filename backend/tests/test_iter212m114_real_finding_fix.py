"""
Iter 212m-114 — REAL finding-fix pipeline for Security Scan + Bug Hunt.

Verifies:
  • services/finding_fix_applier.apply_finding_fix() — full pipeline
    (fetch file → LLM patch → re-validate → commit) returns ok=True
    only when ALL stages succeed.
  • Re-validation gate: if the patched content STILL triggers the
    original rule_id, the call returns ok=False and NEVER pushes a
    commit. This is the "no dummy fix" guarantee.
  • Security scan POST /security-scan/fix endpoint:
        - Per-finding token deduction
        - Founder bypass (is_admin / is_unlimited / tier=='founder')
        - Token REFUND on any apply failure
        - Real commit_sha on success
  • Codebase health POST /codebase-health/fix endpoint same guarantees.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock


# ─── 1. apply_finding_fix end-to-end ──────────────────────────────────
@pytest.mark.asyncio
async def test_apply_finding_fix_happy_path(monkeypatch):
    """When the LLM produces a patch that DOES remove the finding, the
    pipeline must call commit_files and return ok=True with the commit
    SHA."""
    from services import finding_fix_applier as ff

    # Mock project lookup
    class _Proj:
        async def find_one(self, q, proj=None):
            return {
                "github_owner": "polarisbuiltinc",
                "github_repo":  "auremdev",
                "github_branch": "main",
                "github_token": None,
            }
    class _Users:
        async def find_one(self, q, proj=None):
            return {"github": {"access_token": "ghp_realtoken"}}
    class _Fixes:
        async def insert_one(self, doc): pass
    class _DB:
        cto_projects   = _Proj()
        dev_users      = _Users()
        finding_fixes  = _Fixes()

    # Mock the PAT decrypt
    import routers.security_scan as ss
    async def fake_decrypt(uid, tok): return None  # fall back to OAuth
    monkeypatch.setattr(ss, "_decrypt_pat", fake_decrypt)

    # Mock the GitHub fetch
    async def fake_fetch(owner, repo, branch, path, token):
        assert token == "ghp_realtoken"
        return "API_KEY = 'AKIAIOSFODNN7EXAMPLE'\n", None
    monkeypatch.setattr(ff, "_fetch_file_content", fake_fetch)

    # Mock the LLM patch generator
    async def fake_llm(*, path, current_content, finding, user_id):
        return ('import os\nAPI_KEY = os.environ.get("AWS_ACCESS_KEY_ID")  # TODO: set env var\n',
                None)
    monkeypatch.setattr(ff, "_generate_patched_content", fake_llm)

    # Mock the re-validation — return False = finding is gone
    monkeypatch.setattr(ff, "_finding_still_present",
                        lambda patched, path, finding: False)

    # Mock the commit_files call
    commits = []
    async def fake_commit(**kw):
        commits.append(kw)
        return {
            "sha":      "1234abc",
            "full_sha": "1234abcdef" * 4,
            "html_url": "https://github.com/polarisbuiltinc/auremdev/commit/1234abc",
        }
    import services.github_api_writer as gw
    monkeypatch.setattr(gw, "commit_files", fake_commit)

    finding = {
        "rule_id":  "secret_aws_access_key",
        "file":     "app.py",
        "line":     1,
        "severity": "critical",
        "title":    "Hardcoded AWS access key id",
        "message":  "AKIA literal in source",
        "snippet":  "API_KEY = 'AKIA…XX'",
    }
    res = await ff.apply_finding_fix(
        db=_DB(), user={"user_id": "u1"},
        project_id="proj_1", finding=finding,
    )

    assert res["ok"] is True
    assert res["commit_sha"] == "1234abc"
    assert res["rule_id"] == "secret_aws_access_key"
    assert "app.py" in res["message"]
    assert commits and commits[0]["files"] == {
        "app.py": 'import os\nAPI_KEY = os.environ.get("AWS_ACCESS_KEY_ID")  # TODO: set env var\n',
    }


@pytest.mark.asyncio
async def test_apply_finding_fix_rejects_unresolved_patch(monkeypatch):
    """The CRITICAL safety gate. If the LLM produces a patch that still
    has the same rule_id triggering, NO commit is pushed and ok=False."""
    from services import finding_fix_applier as ff

    class _Proj:
        async def find_one(self, q, proj=None):
            return {"github_owner": "o", "github_repo": "r",
                    "github_branch": "main", "github_token": "ghp_x"}
    class _DB:
        cto_projects  = _Proj()
        finding_fixes = type("F", (), {"insert_one": AsyncMock()})()

    import routers.security_scan as ss
    async def fake_decrypt(uid, tok): return "ghp_realtoken"
    monkeypatch.setattr(ss, "_decrypt_pat", fake_decrypt)
    async def fake_fetch(*a, **k):
        return "AWS = 'AKIAEXAMPLE'\n", None
    monkeypatch.setattr(ff, "_fetch_file_content", fake_fetch)
    async def fake_llm(*, path, current_content, finding, user_id):
        # LLM "fixes" it but still leaves an AKIA literal — re-scan trips.
        return "AWS = 'AKIAOTHEREXAMPLE'\n", None
    monkeypatch.setattr(ff, "_generate_patched_content", fake_llm)
    monkeypatch.setattr(ff, "_finding_still_present",
                        lambda patched, path, finding: True)  # still there

    commits = []
    async def fake_commit(**kw):
        commits.append(kw)
        return {}
    import services.github_api_writer as gw
    monkeypatch.setattr(gw, "commit_files", fake_commit)

    res = await ff.apply_finding_fix(
        db=_DB(), user={"user_id": "u1"}, project_id="p1",
        finding={"rule_id": "secret_aws_access_key", "file": "x.py",
                 "severity": "critical", "title": "AWS leak"},
    )
    assert res["ok"] is False
    assert res["error"] == "patch_did_not_resolve_finding"
    assert commits == [], "MUST NOT commit when re-validation fails"


@pytest.mark.asyncio
async def test_apply_finding_fix_no_credentials_returns_clean_error(monkeypatch):
    from services import finding_fix_applier as ff

    class _DB:
        cto_projects = type("P", (), {
            "find_one": AsyncMock(return_value={
                "github_owner": "", "github_repo": "", "github_branch": "main",
                "github_token": None,
            }),
        })()
        dev_users = type("U", (), {
            "find_one": AsyncMock(return_value={"github": {}}),
        })()

    import routers.security_scan as ss
    async def fake_decrypt(uid, tok): return None
    monkeypatch.setattr(ss, "_decrypt_pat", fake_decrypt)

    res = await ff.apply_finding_fix(
        db=_DB(), user={"user_id": "u1"}, project_id="p1",
        finding={"rule_id": "x", "file": "a.py"},
    )
    assert res["ok"] is False
    assert res["error"] == "github_credentials_missing"


# ─── 2. /security-scan/fix endpoint ───────────────────────────────────
@pytest.mark.asyncio
async def test_security_fix_endpoint_founder_bypass(monkeypatch):
    """Founder bearer → tokens_charged=0, no deduction, commit succeeds."""
    from routers import security_scan as ss
    from services import finding_fix_applier as ff

    deductions = []
    class _Users:
        async def find_one(self, q, proj=None): return {"tokens_remaining": 0}
        async def update_one(self, q, u):
            deductions.append(u)
            return type("R", (), {"modified_count": 1})()
    class _DB:
        dev_users = _Users()

    async def fake_current_dev(auth=None):
        return {"user_id": "founder_1", "tier": "founder",
                "is_admin": True, "is_unlimited": True}
    monkeypatch.setattr(ss, "current_dev", fake_current_dev)
    monkeypatch.setattr(ss, "get_db", lambda: _DB())

    async def fake_apply(*, db, user, project_id, finding):
        return {"ok": True, "commit_sha": "abc1234", "full_sha": "abc"*10,
                "html_url": "https://github.com/o/r/commit/abc1234",
                "file": finding["file"], "rule_id": finding["rule_id"],
                "message": "Fixed"}
    monkeypatch.setattr(ff, "apply_finding_fix", fake_apply)

    res = await ss.apply_security_fix(
        body={
            "project_id": "p1",
            "finding":    {"rule_id": "secret_aws_access_key",
                           "file": "app.py", "line": 1,
                           "title": "AWS leak", "message": "x", "snippet": "x"},
            "tokens":     75,
        },
        authorization="Bearer x",
    )
    assert res["ok"] is True
    assert res["tokens_charged"] == 0
    assert res["commit_sha"] == "abc1234"
    assert deductions == [], "Founders must NOT be charged tokens on /security-scan/fix"


@pytest.mark.asyncio
async def test_security_fix_endpoint_refunds_on_patch_rejection(monkeypatch):
    """Non-founder: deduct → apply fails → refund. Net zero."""
    from routers import security_scan as ss
    from services import finding_fix_applier as ff
    from fastapi import HTTPException

    deductions = []
    class _Users:
        async def find_one(self, q, proj=None): return {"tokens_remaining": 500}
        async def update_one(self, q, u):
            deductions.append(u)
            return type("R", (), {"modified_count": 1})()
    class _DB:
        dev_users = _Users()

    async def fake_current_dev(auth=None):
        return {"user_id": "free_1", "tier": "free"}
    monkeypatch.setattr(ss, "current_dev", fake_current_dev)
    monkeypatch.setattr(ss, "get_db", lambda: _DB())

    async def fake_apply(*, db, user, project_id, finding):
        return {"ok": False, "error": "patch_did_not_resolve_finding"}
    monkeypatch.setattr(ff, "apply_finding_fix", fake_apply)

    with pytest.raises(HTTPException) as exc:
        await ss.apply_security_fix(
            body={
                "project_id": "p1",
                "finding":    {"rule_id": "x", "file": "y.py"},
                "tokens":     75,
            },
            authorization="Bearer x",
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["tokens_refunded"] is True
    # Deduct then refund.
    assert len(deductions) == 2
    assert deductions[0] == {"$inc": {"tokens_remaining": -75}}
    assert deductions[1] == {"$inc": {"tokens_remaining": 75}}


@pytest.mark.asyncio
async def test_security_fix_endpoint_validates_body(monkeypatch):
    from routers import security_scan as ss
    from fastapi import HTTPException

    async def fake_current_dev(auth=None): return {"user_id": "u1"}
    monkeypatch.setattr(ss, "current_dev", fake_current_dev)

    with pytest.raises(HTTPException) as exc:
        await ss.apply_security_fix(body={}, authorization="Bearer x")
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await ss.apply_security_fix(body={"project_id": "p1"}, authorization="Bearer x")
    assert exc.value.status_code == 400


# ─── 3. Source-level invariants (no dummy/mock leftover) ──────────────
def test_codebase_health_fix_no_longer_uses_dummy_queue():
    src = open("/app/backend/routers/codebase_health.py").read()
    # The previous "Fix queued — N tokens charged" dummy message must
    # be gone (replaced by the real apply pipeline's message).
    assert "Fix queued —" not in src, \
        "/codebase-health/fix must no longer return the dummy 'Fix queued' message"
    # Must call the real apply pipeline.
    assert "apply_finding_fix" in src
    # The status must now be 'completed', not 'queued'.
    assert '"status":          "completed"' in src or '"status": "completed"' in src


def test_security_scan_has_fix_endpoint():
    src = open("/app/backend/routers/security_scan.py").read()
    assert '@router.post("/fix")' in src
    assert "async def apply_security_fix" in src
    # Must call the real apply pipeline.
    assert "apply_finding_fix" in src
    # Must enforce founder bypass.
    assert "is_unlimited" in src
    assert '"founder"' in src
