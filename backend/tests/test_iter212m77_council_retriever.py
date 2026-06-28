"""
Iter 212m-77 — Tests for the ORA Council RAG retriever.

Verifies:
  1. Below-threshold corpus returns empty (no false learning).
  2. Above-threshold corpus returns a formatted few-shot block.
  3. Quality filter excludes lint_blocked / failed-pass rows.
  4. Per-user / per-mode bucketing.
  5. Empty user_message returns empty string.
  6. Errors inside the retriever NEVER propagate out (chat safety).
  7. Stats shape includes the `active` + `corpus_rows` keys.
"""
from datetime import datetime, timezone
import pytest

import services.ora_council_retriever as r


class _AsyncCursor:
    def __init__(self, rows): self._rows = rows
    def sort(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def __aiter__(self): self._i = 0; return self
    async def __anext__(self):
        if self._i >= len(self._rows): raise StopAsyncIteration
        v = self._rows[self._i]; self._i += 1; return v


class _FakeColl:
    def __init__(self, rows): self.rows = rows
    def find(self, q=None, proj=None): return _AsyncCursor(self.rows)


class _FakeDB:
    def __init__(self, rows): self._rows = rows
    def __getitem__(self, name): return _FakeColl(self._rows)


def _row(msg, reply, mode="A", user="u1", project="p1",
         pass_result=True, lint_blocked=False):
    return {
        "user_message": msg, "final_output": reply, "mode": mode,
        "user_id": user, "project_id": project,
        "pass_result": pass_result, "lint_blocked": lint_blocked,
        "timestamp": datetime.now(timezone.utc),
    }


@pytest.fixture(autouse=True)
def _reset(): r._reset_for_tests(); yield; r._reset_for_tests()


@pytest.mark.asyncio
async def test_below_threshold_returns_empty():
    # Only 3 rows, threshold is 5.
    db = _FakeDB([_row(f"q{i}", f"a{i}") for i in range(3)])
    block, n = await r.get_council_few_shot(db, "how do I add a button?")
    assert block == ""
    assert n == 0
    s = r.get_retriever_stats()
    assert s["active"] is False
    assert s["corpus_rows"] == 3


@pytest.mark.asyncio
async def test_returns_few_shot_block_when_active():
    rows = [
        _row("How do I add a button to React?",
             "Use a <button> element with onClick handler."),
        _row("How to install Redis on docker?",
             "Run docker run -d -p 6379:6379 redis."),
        _row("React state update best practice",
             "Use useState hook with a function updater."),
        _row("Adding a button in HTML",
             "<button type='button'>Click</button>"),
        _row("React onClick handler example",
             "<button onClick={handler}>Click</button>"),
        _row("Pydantic v2 model basic example",
             "class Item(BaseModel): name: str"),
    ]
    db = _FakeDB(rows)
    out, n = await r.get_council_few_shot(
        db, "add an onClick button in React", mode="A", k=2,
    )
    assert "[ORA COUNCIL — LEARNED EXAMPLES]" in out
    assert "Past example #1" in out
    assert "Past example #2" in out
    assert n == 2
    # The two most relevant matches mention React + button + onClick.
    assert "React" in out or "button" in out or "onClick" in out


@pytest.mark.asyncio
async def test_quality_filter_excludes_lint_blocked():
    rows = [
        _row(f"q{i}", f"a{i}", mode="C", lint_blocked=True)
        for i in range(10)
    ] + [
        _row("good msg", "good answer", mode="C", pass_result=True),
    ]
    db = _FakeDB(rows)
    await r.get_council_few_shot(db, "any", mode="C")
    assert r._index["row_count"] == 1   # 10 lint-blocked were filtered


@pytest.mark.asyncio
async def test_quality_filter_excludes_failed_code_runs():
    rows = (
        [_row(f"x{i}", "ok", mode="C", pass_result=False) for i in range(8)]
        + [_row("good code task", "passed",
                mode="C", pass_result=True) for _ in range(2)]
    )
    db = _FakeDB(rows)
    await r.get_council_few_shot(db, "code", mode="C")
    assert r._index["row_count"] == 2


@pytest.mark.asyncio
async def test_empty_query_returns_empty():
    rows = [_row(f"q{i}", f"a{i}") for i in range(10)]
    db = _FakeDB(rows)
    out, n = await r.get_council_few_shot(db, "", mode="A")
    assert out == ""
    assert n == 0


@pytest.mark.asyncio
async def test_retriever_safe_on_db_error():
    class _BrokenDB:
        def __getitem__(self, name):
            raise RuntimeError("DB exploded")
    out, n = await r.get_council_few_shot(_BrokenDB(), "anything")
    assert out == ""   # NEVER raises
    assert n == 0


def test_get_retriever_stats_shape():
    s = r.get_retriever_stats()
    assert "active" in s
    assert "corpus_rows" in s
    assert "modes_indexed" in s
    assert "min_global_threshold" in s
    assert s["min_global_threshold"] == 5
    assert s["min_bucket_threshold"] == 20


@pytest.mark.asyncio
async def test_top_k_capped():
    rows = [_row(f"question about react state {i}",
                 f"answer {i}") for i in range(30)]
    db = _FakeDB(rows)
    out, n = await r.get_council_few_shot(
        db, "react state question", mode="A", k=3,
    )
    # Block must mention exactly 3 past examples.
    assert "Past example #1" in out
    assert "Past example #2" in out
    assert "Past example #3" in out
    assert "Past example #4" not in out
    assert n == 3
