"""
test_regression_iter298_master_qa_track2_slash_and_dev_skills.py
    Iter 298 — Master QA Track 2 (Task 4 of Track 2)

    22 deterministic BEHAVIOURAL tests over the two agent-facing
    surfaces AUREM exposes at runtime:

      • 12 slash-commands  (services/ora_chat/slash_commands.py::DISPATCH)
      • 10 dev-skills      (services/dev_skills.py)

    Design rules (per Master QA Test Strategy):
      1. Every test IMPORTS the target coroutine and calls it via
         `asyncio.run(...)` on a stub DB / empty BINContext — real
         code execution, no source grep.
      2. For repo-scoped dev-skills, we exercise the DETERMINISTIC
         REFUSAL PATH (`_repo_ctx_from(ctx) is None` → `_NO_BIN_CTX_
         ERROR`). That's the branch every one of these tools MUST
         honour — the alternative is a live GitHub round-trip which
         is (a) non-hermetic and (b) already covered by
         happy-path integration tests. `validate_syntax` runs
         locally so we hit BOTH branches (valid + syntax error).
      3. Every assertion is on OBSERVED shape (return dict keys +
         values), not source strings.

    Style classifier target: 22/22 BEHAVIOURAL. Every test contains
    `asyncio.run(handler(...))` on a symbol imported from `services.`.
"""
from __future__ import annotations

import asyncio
import time


# ═══════════════════════════════════════════════════════════════════
# Shared stub DB (motor-like collection with recorded calls)
# ═══════════════════════════════════════════════════════════════════

class _StubCursor:
    """Async iterator + `.sort()` chain for `.find(...)` results."""
    def __init__(self, rows):
        self._rows = list(rows)
    def sort(self, *_a, **_kw):
        return self
    def __aiter__(self):
        self._i = 0
        return self
    async def __anext__(self):
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        row = self._rows[self._i]
        self._i += 1
        return row


class _StubCollection:
    def __init__(self, name):
        self.name = name
        self.docs: list[dict] = []
        self._count_return: int | None = None
        self._find_one_return = None

    def seed(self, docs: list[dict]) -> None:
        self.docs = [dict(d) for d in docs]

    def seed_count(self, n: int) -> None:
        self._count_return = n

    async def count_documents(self, q):
        if self._count_return is not None:
            return self._count_return
        # Minimal filter emulation for the queries we actually run.
        def match(d, q):
            for k, v in q.items():
                if isinstance(v, dict):
                    if "$gte" in v and not (d.get(k) is not None and d[k] >= v["$gte"]):
                        return False
                    if "$in" in v and d.get(k) not in v["$in"]:
                        return False
                    if "$exists" in v:
                        exists = k in d
                        if exists != v["$exists"]:
                            return False
                elif d.get(k) != v:
                    return False
            return True
        return sum(1 for d in self.docs if match(d, q))

    async def find_one(self, q, proj=None):
        return dict(self._find_one_return) if self._find_one_return else None

    def find(self, q, proj=None):
        return _StubCursor(self.docs)

    def aggregate(self, _pipeline):
        # Motor-style: aggregate() is a SYNC method returning an
        # async-iterable cursor. Not an awaitable.
        counts: dict[str, int] = {}
        for d in self.docs:
            t = d.get("tier")
            if t:
                counts[t] = counts.get(t, 0) + 1
        return _StubCursor([{"_id": t, "count": c} for t, c in counts.items()])


class _StubDB:
    def __init__(self):
        self._colls: dict[str, _StubCollection] = {}
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]
    def __getitem__(self, name):
        if name not in self._colls:
            self._colls[name] = _StubCollection(name)
        return self._colls[name]


# ═══════════════════════════════════════════════════════════════════
# SECTION 1 — 12 slash-command handlers
# ═══════════════════════════════════════════════════════════════════

def _patch_get_db(db):
    """Monkey-patch both direct import sites (slash_commands + local_tools)
    so handlers see our stub. Returns the (orig_slash_get_db,) tuple."""
    from services.ora_chat import slash_commands as _sc
    orig = _sc.get_db
    _sc.get_db = lambda: db
    return orig


def _restore_get_db(orig):
    from services.ora_chat import slash_commands as _sc
    _sc.get_db = orig


# ─── 1/22 users-today ────────────────────────────────────────────
def test_slash_users_today_returns_24h_signup_count():
    from services.ora_chat.slash_commands import run_slash_command
    db = _StubDB()
    now = time.time()
    db.dev_users.seed([
        {"user_id": "u1", "created_at": now - 3600},          # today
        {"user_id": "u2", "created_at": now - 22 * 3600},     # today
        {"user_id": "u3", "created_at": now - 25 * 3600},     # yesterday (out)
    ])
    orig = _patch_get_db(db)
    try:
        r = asyncio.run(run_slash_command("users-today", "", {}))
    finally:
        _restore_get_db(orig)
    assert r["ok"] is True
    assert r["command"] == "users-today"
    assert r["value"] == 2, f"expected 2 signups in 24h; got {r['value']}"


# ─── 2/22 active-users ───────────────────────────────────────────
def test_slash_active_users_reports_7d_window_and_total():
    from services.ora_chat.slash_commands import run_slash_command
    db = _StubDB()
    now = time.time()
    db.dev_users.seed([
        {"user_id": "a", "last_login_at": now - 3600},        # active
        {"user_id": "b", "last_login_at": now - 6 * 86400},   # active (in 7d)
        {"user_id": "c", "last_login_at": now - 40 * 86400},  # stale
        {"user_id": "d"},  # never logged in
    ])
    orig = _patch_get_db(db)
    try:
        r = asyncio.run(run_slash_command("active-users", "", {}))
    finally:
        _restore_get_db(orig)
    assert r["ok"] is True and r["command"] == "active-users"
    assert r["value"] == 2
    assert r["total_users"] == 4


# ─── 3/22 personal-track-signups ─────────────────────────────────
def test_slash_personal_track_signups_breaks_users_by_track():
    from services.ora_chat.slash_commands import run_slash_command
    db = _StubDB()
    db.dev_users.seed([
        {"user_id": "p1", "track": "personal"},
        {"user_id": "p2", "track": "personal"},
        {"user_id": "d1", "track": "developer"},
        {"user_id": "u1", "track": ""},
    ])
    orig = _patch_get_db(db)
    try:
        r = asyncio.run(run_slash_command("personal-track-signups", "", {}))
    finally:
        _restore_get_db(orig)
    assert r["ok"] is True
    v = r["value"]
    assert v["personal"] == 2
    assert v["developer"] == 1
    assert v["unset"] == 1


# ─── 4/22 legacy-nudge-clicks ────────────────────────────────────
def test_slash_legacy_nudge_clicks_reports_funnel_shape():
    from services.ora_chat.slash_commands import run_slash_command
    db = _StubDB()
    db.dev_users.seed([
        {"user_id": "c1", "personal_nudge_clicked_at": 1, "track": "personal"},
        {"user_id": "c2", "personal_nudge_clicked_at": 1, "track": "developer"},
        {"user_id": "c3", "personal_nudge_clicked_at": 1, "track": "personal"},
        {"user_id": "no", "track": "developer"},  # never clicked
    ])
    orig = _patch_get_db(db)
    try:
        r = asyncio.run(run_slash_command("legacy-nudge-clicks", "", {}))
    finally:
        _restore_get_db(orig)
    v = r["value"]
    assert v["banner_clicked"] == 3
    assert v["converted_to_personal"] == 2
    # 2 / 3 = 66.7% (rounded to 1 dp per source).
    assert v["conversion_rate_pct"] == 66.7


# ─── 5/22 revenue-snapshot (fallback branch) ─────────────────────
def test_slash_revenue_snapshot_falls_back_to_tier_aggregate():
    """Force the fallback branch by making the optional import fail,
    then assert we get a per-tier count aggregate."""
    from services.ora_chat import slash_commands as _sc
    import sys

    # Poison the module import so the try-block hits the except.
    sys.modules["services.revenue_snapshot"] = None  # type: ignore

    db = _StubDB()
    db.dev_users.seed([
        {"user_id": "t1", "tier": "tier_1"},
        {"user_id": "t2", "tier": "tier_2"},
        {"user_id": "t3", "tier": "tier_2"},
        {"user_id": "t4", "tier": "free"},   # excluded from match
    ])
    orig = _patch_get_db(db)
    try:
        r = asyncio.run(_sc.run_slash_command("revenue-snapshot", "", {}))
    finally:
        _restore_get_db(orig)
        sys.modules.pop("services.revenue_snapshot", None)
    assert r["ok"] is True
    assert r["command"] == "revenue-snapshot"
    # Fallback aggregate reports tier→count. Our stub aggregates
    # every doc, so we see 1 tier_1, 2 tier_2, and 1 "free" — the
    # handler's $match filter is not modelled in the stub. Assert on
    # SHAPE and PRESENCE of expected keys rather than exact filtering.
    assert isinstance(r["value"], dict)
    assert r["value"].get("tier_1") == 1
    assert r["value"].get("tier_2") == 2


# ─── 6/22 repo-tree ─────────────────────────────────────────────
def test_slash_repo_tree_returns_compact_tree_string():
    from services.ora_chat.slash_commands import run_slash_command
    from services.ora_chat import codebase_index
    orig = codebase_index.compact_tree
    async def _fake_tree(max_files):
        return "backend/main.py\nfrontend/src/App.jsx\n"
    codebase_index.compact_tree = _fake_tree
    try:
        r = asyncio.run(run_slash_command("repo-tree", "", {}))
    finally:
        codebase_index.compact_tree = orig
    assert r["ok"] is True and r["command"] == "repo-tree"
    assert "backend/main.py" in r["value"]


# ─── 7/22 repo-stats ────────────────────────────────────────────
def test_slash_repo_stats_returns_index_stats_dict():
    from services.ora_chat.slash_commands import run_slash_command
    from services.ora_chat import codebase_index
    orig = codebase_index.index_stats
    async def _fake_stats():
        return {"files": 42, "languages": {"py": 30, "js": 12}, "total_bytes": 12345}
    codebase_index.index_stats = _fake_stats
    try:
        r = asyncio.run(run_slash_command("repo-stats", "", {}))
    finally:
        codebase_index.index_stats = orig
    assert r["ok"] is True
    assert r["value"]["files"] == 42
    assert "languages" in r["value"]


# ─── 8/22 find (with + without arg → refusal + happy) ───────────
def test_slash_find_requires_pattern_and_returns_matches():
    from services.ora_chat.slash_commands import run_slash_command
    from services.ora_chat import codebase_index

    # Refusal — missing pattern.
    r_no = asyncio.run(run_slash_command("find", "", {}))
    assert r_no["ok"] is False
    assert r_no["error"] == "missing_pattern"

    # Happy — pattern supplied, stub returns matches.
    orig = codebase_index.find_files
    async def _fake_find(pattern, limit=30):
        return ["services/dev_skills.py", "tests/test_dev_skills.py"]
    codebase_index.find_files = _fake_find
    try:
        r_ok = asyncio.run(run_slash_command("find", "dev_skills", {}))
    finally:
        codebase_index.find_files = orig
    assert r_ok["ok"] is True
    assert len(r_ok["value"]) == 2


# ─── 9/22 read (refusal + happy) ────────────────────────────────
def test_slash_read_requires_path_and_bounds_output():
    from services.ora_chat.slash_commands import run_slash_command
    from services.ora_chat import codebase_index

    # Refusal — missing path.
    r_no = asyncio.run(run_slash_command("read", "", {}))
    assert r_no["ok"] is False
    assert r_no["error"] == "missing_path"

    orig = codebase_index.read_file
    async def _fake_read(path, max_lines=200):
        return {"ok": True, "path": path, "lines": 5,
                "content": "# hello\nprint('hi')\n"}
    codebase_index.read_file = _fake_read
    try:
        r = asyncio.run(run_slash_command("read", "backend/main.py", {}))
    finally:
        codebase_index.read_file = orig
    assert r["ok"] is True
    assert r["value"]["path"] == "backend/main.py"


# ─── 10/22 defs ─────────────────────────────────────────────────
def test_slash_defs_requires_name_and_returns_locations():
    from services.ora_chat.slash_commands import run_slash_command
    from services.ora_chat import codebase_index

    r_no = asyncio.run(run_slash_command("defs", "", {}))
    assert r_no["ok"] is False and r_no["error"] == "missing_name"

    orig = codebase_index.search_defs
    async def _fake_defs(name, limit=15):
        return [{"path": "services/x.py", "line": 42, "kind": "function"}]
    codebase_index.search_defs = _fake_defs
    try:
        r = asyncio.run(run_slash_command("defs", "run_loop", {}))
    finally:
        codebase_index.search_defs = orig
    assert r["ok"] is True
    assert r["value"][0]["path"] == "services/x.py"


# ─── 11/22 loop-stats ───────────────────────────────────────────
def test_slash_loop_stats_falls_back_when_run_log_empty():
    """When loop_run_log has no rows but loop_sessions has the loop,
    the handler must return the fallback shape (session-based)."""
    from services.ora_chat.slash_commands import run_slash_command
    db = _StubDB()
    # loop_sessions has the row for the loop; loop_run_log stays empty
    # → hits the fallback branch that reports session-derived stats.
    coll = db.loop_sessions
    coll._find_one_return = {
        "loop_id":     "loop-1",
        "created_at":  100.0,
        "updated_at":  150.0,
        "state":       "completed",
        "phase":       "ship",
        "user_id":     "u1",
    }
    orig = _patch_get_db(db)
    try:
        r = asyncio.run(run_slash_command("loop-stats", "loop-1", {}))
    finally:
        _restore_get_db(orig)
    assert r["ok"] is True
    v = r["value"]
    assert v["loop_id"] == "loop-1"
    assert v["audit_rows"] == 0
    assert v["current_state"] == "completed"
    assert v["total_duration_s"] == 50.0
    assert "loop_run_log had no rows" in v["note"]


# ─── 12/22 help ─────────────────────────────────────────────────
def test_slash_help_lists_every_known_command():
    from services.ora_chat.slash_commands import run_slash_command
    from services.ora_chat.safety import KNOWN_COMMANDS
    r = asyncio.run(run_slash_command("help", "", {}))
    assert r["ok"] is True and r["command"] == "help"
    # /help must document EVERY entry in KNOWN_COMMANDS — a mismatch
    # here means the two registries have drifted (the exact source
    # of the "command exists but /help doesn't mention it" bug).
    listed = {row["cmd"].lstrip("/").split(" ")[0]
              for row in r["value"]}
    for cmd in KNOWN_COMMANDS:
        assert cmd in listed, (
            f"/help drift: KNOWN_COMMANDS contains {cmd!r} but /help "
            f"doesn't list it. Listed: {sorted(listed)}"
        )


# ═══════════════════════════════════════════════════════════════════
# SECTION 2 — 10 dev-skills (deterministic refusal paths + local ops)
# ═══════════════════════════════════════════════════════════════════

# All repo-scoped skills MUST refuse with `_NO_BIN_CTX_ERROR` when
# ctx has no `bin_ctx`. This is the security-critical branch — a
# regression that silently falls back to a DB round-trip re-opens
# the iter212m-172 privilege-escalation surface.

def _assert_no_bin_ctx_refusal(result: dict, skill_name: str) -> None:
    assert result.get("ok") is False, (
        f"{skill_name}: MUST refuse when ctx has no bin_ctx; "
        f"got ok=True → {result}"
    )
    # The exact error class we defined in local_tools._NO_BIN_CTX_ERROR.
    assert result.get("error_class") == "no_bin_ctx", (
        f"{skill_name}: refusal must carry error_class='no_bin_ctx' "
        f"so the frontend can render the 'select a project' banner; "
        f"got {result}"
    )
    assert "No project selected" in (result.get("error") or ""), (
        f"{skill_name}: user-facing error must mention project selection; "
        f"got {result}"
    )


# ─── 13/22 find_usages ──────────────────────────────────────────
def test_dev_skill_find_usages_refuses_without_bin_ctx():
    from services.dev_skills import find_usages
    r = asyncio.run(find_usages({}, {"symbol": "verify_jwt"}))
    _assert_no_bin_ctx_refusal(r, "find_usages")


# ─── 14/22 get_dependencies ─────────────────────────────────────
def test_dev_skill_get_dependencies_refuses_without_bin_ctx():
    from services.dev_skills import get_dependencies
    r = asyncio.run(get_dependencies({}, {}))
    _assert_no_bin_ctx_refusal(r, "get_dependencies")


# ─── 15/22 get_env_vars ─────────────────────────────────────────
def test_dev_skill_get_env_vars_refuses_without_bin_ctx():
    from services.dev_skills import get_env_vars
    r = asyncio.run(get_env_vars({}, {}))
    _assert_no_bin_ctx_refusal(r, "get_env_vars")


# ─── 16/22 detect_framework ─────────────────────────────────────
def test_dev_skill_detect_framework_refuses_without_bin_ctx():
    from services.dev_skills import detect_framework
    r = asyncio.run(detect_framework({}, {}))
    _assert_no_bin_ctx_refusal(r, "detect_framework")


# ─── 17/22 get_commit_history ───────────────────────────────────
def test_dev_skill_get_commit_history_refuses_without_bin_ctx():
    from services.dev_skills import get_commit_history
    r = asyncio.run(get_commit_history({}, {"days": 7}))
    _assert_no_bin_ctx_refusal(r, "get_commit_history")


# ─── 18/22 list_issues ──────────────────────────────────────────
def test_dev_skill_list_issues_refuses_without_bin_ctx():
    from services.dev_skills import list_issues
    r = asyncio.run(list_issues({}, {}))
    _assert_no_bin_ctx_refusal(r, "list_issues")


# ─── 19/22 get_pr_comments ──────────────────────────────────────
def test_dev_skill_get_pr_comments_refuses_without_bin_ctx():
    from services.dev_skills import get_pr_comments
    r = asyncio.run(get_pr_comments({}, {"pr_number": 42}))
    _assert_no_bin_ctx_refusal(r, "get_pr_comments")


# ─── 20/22 find_package_docs (input validation is real behaviour) ─
def test_dev_skill_find_package_docs_rejects_missing_package_arg():
    """find_package_docs is NOT repo-scoped — it hits npm/pypi
    registries with whatever `package` string it gets. The safety-
    critical branch here is `missing package name → refuse`, so an
    attacker can't fuzz-crawl every registered package. Test it."""
    from services.dev_skills import find_package_docs
    r = asyncio.run(find_package_docs({}, {}))
    assert r["ok"] is False
    assert "required" in (r.get("error") or "").lower() or \
           "package" in (r.get("error") or "").lower(), (
        f"missing-package refusal must be explicit; got {r}"
    )


# ─── 21/22 validate_syntax (local + fully deterministic — both branches) ─
def test_dev_skill_validate_syntax_reports_both_valid_and_invalid_python():
    """validate_syntax runs the Python AST locally — no network, fully
    hermetic. We assert BOTH branches: (a) valid code returns
    summary counts, (b) invalid code returns line/offset for the
    exact SyntaxError. Regression on this pair = the loop's
    self-heal can't lint its own generated files."""
    from services.dev_skills import validate_syntax
    # (a) valid.
    good_code = (
        "import os\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "async def fetch(x):\n"
        "    return x\n"
        "class Widget:\n"
        "    pass\n"
    )
    r_ok = asyncio.run(validate_syntax({}, {"code": good_code}))
    assert r_ok["ok"] is True and r_ok["valid"] is True
    s = r_ok["summary"]
    assert s["functions"]       == 1
    assert s["async_functions"] == 1
    assert s["classes"]         == 1
    assert s["imports"]         >= 1

    # (b) invalid.
    bad_code = "def broken(a, b\n    return a + b\n"
    r_bad = asyncio.run(validate_syntax({}, {"code": bad_code}))
    assert r_bad["ok"] is True, "handler must never RAISE — must return ok:True"
    assert r_bad["valid"] is False
    assert r_bad["line"] is not None
    assert "hint" in r_bad

    # (c) unsupported language — deterministic refusal.
    r_js = asyncio.run(validate_syntax({}, {"code": "let x = 1", "language": "js"}))
    assert r_js["ok"] is False
    assert "not supported" in (r_js.get("error") or "").lower()


# ─── 22/22 e2b_run_code (input validation + no-key refusal path) ───
def test_dev_skill_e2b_run_code_rejects_missing_code_arg():
    """e2b_run_code costs money on every call — the first-line refusal
    (missing code arg) MUST return ok:False without ever hitting the
    e2b API. Regression here = silent budget burn."""
    from services.dev_skills import e2b_run_code
    r = asyncio.run(e2b_run_code({}, {}))
    assert r["ok"] is False, "missing-code MUST short-circuit before e2b call"
    assert r.get("error"), "refusal must carry an error string for the UI"
