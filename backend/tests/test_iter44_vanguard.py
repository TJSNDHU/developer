"""
tests/test_iter44_vanguard.py
=============================
Iter 44 — Vanguard 007 scanner + skill context injector.
"""
from __future__ import annotations
import pytest


# ─── Vanguard 007 secret scanner ────────────────────────────────────────

class TestVanguardScanner:
    def test_catches_github_pat(self):
        from services.vanguard_scanner import scan_text, has_critical
        bad = 'TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"'
        f = scan_text(bad, filepath="config.py")
        assert len(f) >= 1
        # github_token regex hits the literal PAT, token_assignment hits the assignment
        names = {x["name"] for x in f}
        assert "github_token" in names or "token_assignment" in names
        assert has_critical(f) is True

    def test_catches_aws_access_key(self):
        from services.vanguard_scanner import scan_text, has_critical
        bad = 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"'
        f = scan_text(bad, filepath="config.py")
        assert any(x["name"] == "aws_access_key" for x in f)
        assert has_critical(f)

    def test_catches_openai_key(self):
        from services.vanguard_scanner import scan_text
        bad = 'sk_key = "sk-abc123def456ghi789jkl012mno345pqr678"'
        f = scan_text(bad, filepath="x.py")
        names = {x["name"] for x in f}
        # token_assignment OR openai_key — either is a valid catch
        assert ("openai_key" in names) or ("token_assignment" in names)

    def test_catches_private_key_pem(self):
        from services.vanguard_scanner import scan_text, has_critical
        bad = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...\n-----END..."
        f = scan_text(bad, filepath="x.pem")
        assert any(x["name"] == "private_key" for x in f)
        assert has_critical(f)

    def test_catches_db_connection_string(self):
        from services.vanguard_scanner import scan_text, has_critical
        bad = 'DB = "postgres://admin:supersecret123@db.example.com/prod"'
        f = scan_text(bad, filepath="x.py")
        assert any(x["name"] == "db_connection_string" for x in f)
        assert has_critical(f)

    def test_catches_eval_usage(self):
        from services.vanguard_scanner import scan_text, has_critical
        bad = "def run(code): return eval(code)"
        f = scan_text(bad, filepath="x.py")
        assert any(x["name"] == "eval_usage" for x in f)
        assert has_critical(f)

    def test_catches_subprocess_shell_true(self):
        from services.vanguard_scanner import scan_text, has_critical
        bad = 'subprocess.run("ls " + user_input, shell=True)'
        f = scan_text(bad, filepath="x.py")
        assert any(x["name"] == "subprocess_shell_true" for x in f)
        assert has_critical(f)

    def test_clean_code_no_findings(self):
        from services.vanguard_scanner import scan_text
        good = "def add(a, b):\n    return a + b\n\nprint(add(1, 2))"
        assert scan_text(good, filepath="x.py") == []


# ─── design_linter integration ──────────────────────────────────────────

class TestDesignLinterUsesVanguard:
    def test_lint_blocks_github_pat_via_vanguard(self):
        from services.design_linter import lint_file_blocks
        # Hardcoded GitHub PAT — should be blocked by the new Vanguard
        # layer even though our original linter regex might not catch it
        # in this exact format.
        edits = {
            "backend/config.py":
            'GITHUB_TOKEN = "ghp_TestVanguard123456789012345678901234567890"\n'
        }
        r = lint_file_blocks(edits)
        assert r["blocked"] is True, (
            f"Expected blocked=True with Vanguard scanner, got {r['blocked']}. "
            f"reasons={r.get('block_reasons')}"
        )
        assert any("vg." in i.get("rule", "") or "vanguard" in i.get("rule", "").lower()
                   for i in r["issues"])

    def test_lint_blocks_aws_key_via_vanguard(self):
        from services.design_linter import lint_file_blocks
        edits = {"infra/aws.py": 'KEY = "AKIAIOSFODNN7EXAMPLE"'}
        r = lint_file_blocks(edits)
        assert r["blocked"] is True


# ─── Skill context injector ─────────────────────────────────────────────

class TestSkillInjector:
    def test_auth_task_injects_auth_playbook(self):
        from services.skill_context_injector import select_skills
        names = [n for n, _ in select_skills("add a JWT login endpoint")]
        assert "auth-implementation.md" in names

    def test_payments_task_injects_api_security(self):
        from services.skill_context_injector import select_skills
        names = [n for n, _ in select_skills("integrate Stripe checkout for billing")]
        assert "api-security.md" in names

    def test_react_task_injects_frontend_security(self):
        from services.skill_context_injector import select_skills
        names = [n for n, _ in select_skills("create a React form component")]
        assert "frontend-security.md" in names

    def test_security_review_always_injected(self):
        from services.skill_context_injector import select_skills
        names = [n for n, _ in select_skills("fix typo in README")]
        # Even unrelated tasks get the general security checklist
        assert "security-review.md" in names

    def test_build_skill_context_returns_markdown(self):
        from services.skill_context_injector import build_skill_context
        ctx = build_skill_context("add JWT auth with refresh tokens")
        assert "VANGUARD SECURITY SKILLS" in ctx
        assert len(ctx) > 500   # got real skill content, not empty
        assert len(ctx) < 12000 # but capped by per-skill char limits

    def test_no_match_still_returns_security_review(self):
        from services.skill_context_injector import build_skill_context
        ctx = build_skill_context("hi")
        # Always-inject security review still fires
        assert "SECURITY REVIEW" in ctx.upper()
