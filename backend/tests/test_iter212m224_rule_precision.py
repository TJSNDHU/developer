"""
Iter 212m-224 (part 2) — Scanner rule precision fixes.

Two over-broad rules caused a wave of false-positive CRITICAL findings
in the self-scan report:

1. `exec_usage` (`\\bexec\\s*\\(`) matched JavaScript's
   `RegExp.prototype.exec(...)`. Every `.exec(` in a React app got
   flagged as Python `exec()`.

2. `private_key` (`-----BEGIN … PRIVATE KEY-----`) matched
   placeholder strings in form JSX (`placeholder="-----BEGIN OPENSSH
   PRIVATE KEY-----\\n…\\n-----END…-----"`).

The fixes:
- `exec_usage`  now uses `(?<![.\\w])exec\\s*\\(` so `.exec(` on any
  object (RegExp, iterators, …) is skipped.
- `private_key` requires a base64 key body on the next line
  (`\\n[A-Za-z0-9+/=]{20,}`), so JSX placeholders with `\\n…\\n` in
  between never match.

These tests LOCK the refinement: any future revert would fail.
"""

from __future__ import annotations

import re


def test_exec_usage_regex_skips_regexp_exec():
    from services.vanguard_scanner import DANGEROUS_PATTERNS
    exec_rule = next((p for name, p, sev in DANGEROUS_PATTERNS
                      if name == "exec_usage"), None)
    assert exec_rule is not None, "exec_usage rule missing"

    # Positive — real Python exec()
    assert exec_rule.search("exec(user_supplied_code)") is not None
    assert exec_rule.search("  exec ( '2+2' )") is not None

    # Negative — JavaScript RegExp.prototype.exec()
    js_lines = [
        "FENCE_RE.exec(text)",
        "while ((m = FENCE_RE.exec(text)) !== null)",
        "/attempt\\s+(\\d+)\\b/i.exec(ev.message)",
        "regex.exec(input)",
        "myVar.exec(param)",
    ]
    for ln in js_lines:
        assert exec_rule.search(ln) is None, (
            f"exec_usage FALSE-POSITIVE on JavaScript RegExp.exec: {ln!r}"
        )


def test_private_key_regex_skips_placeholders():
    from services.vanguard_scanner import SECRET_PATTERNS
    pk_rule = next((p for name, p, sev in SECRET_PATTERNS
                    if name == "private_key"), None)
    assert pk_rule is not None, "private_key rule missing"

    # Positive — actual key blob (base64 body on next line)
    real_key = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAABBAAB\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    assert pk_rule.search(real_key) is not None

    # Negative — placeholders with ellipsis / template strings
    placeholders = [
        "-----BEGIN OPENSSH PRIVATE KEY-----\n…\n-----END OPENSSH PRIVATE KEY-----",
        "-----BEGIN OPENSSH PRIVATE KEY-----\n...\n-----END OPENSSH PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----\n<paste key here>\n-----END RSA PRIVATE KEY-----",
    ]
    for p in placeholders:
        assert pk_rule.search(p) is None, (
            f"private_key FALSE-POSITIVE on placeholder: {p!r}"
        )


def test_scan_file_blocks_end_to_end_precision():
    from services.vanguard_scanner import scan_file_blocks
    files = {
        # Real Python exec — MUST flag
        "backend/exec_user_input.py":
            "def run(code):\n    exec(code)\n",
        # JavaScript RegExp.exec — MUST NOT flag
        "frontend/utils.js":
            "const RE = /foo/g;\nwhile ((m = RE.exec(text)) !== null) { }\n",
        # JSX placeholder — MUST NOT flag
        "frontend/Form.jsx":
            'placeholder={"-----BEGIN OPENSSH PRIVATE KEY-----\\n…\\n"}\n',
    }
    findings = scan_file_blocks(files)
    flagged_paths = {f.get("filepath") or f.get("file") for f in findings}
    assert "backend/exec_user_input.py" in flagged_paths, \
        "real Python exec() must still be caught"
    assert "frontend/utils.js" not in flagged_paths, \
        "JavaScript RegExp.exec must not false-positive"
    assert "frontend/Form.jsx" not in flagged_paths, \
        "JSX placeholder must not false-positive"
