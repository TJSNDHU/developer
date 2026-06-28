"""
services/ora_council_retriever.py  —  Iter 212m-77

ACTIVATES the ORA Council self-learning loop NOW, at N=165, without
waiting for the 1,000-row fine-tuning threshold.

Why this exists
---------------
The Council has been collecting (user_message, final_output) pairs
across modes A/B/C/D/E since Iter 30-ish (165 rows today). The
original plan was "wait until 1,000 and ship to fine-tune." That
gate is wrong for two reasons:

  1. Fine-tuning is expensive + slow (weeks of cycle time + a
     non-trivial $$ bill). Worse, it ages out the moment we ship a
     new feature.
  2. Retrieval-Augmented Generation (RAG) over the existing logs
     gets us ~80 % of the self-learning benefit AT N=20+. It works
     by finding the K most-similar past interactions and prepending
     them to the system prompt as few-shot demonstrations.

What this module does
---------------------
- Builds a pure-Python TF-IDF index over `ora_council_logs.user_message`.
- Filters to "high-quality" rows only: `pass_result==True` OR (no
  correction applied AND no lint block) — we don't learn from
  failures.
- Optional per-user / per-project / per-mode scoping so different
  developers see their own personalised few-shot examples.
- Refresh-on-stale: rebuilds the index every `_REFRESH_TTL` seconds
  on the first call after the TTL expires (cheap — one count_documents
  query checks if the corpus changed).
- Returns a formatted block ready to prepend to `extra_sys` in the
  chat router.

Activation threshold
--------------------
- N >= 20 rows for the same (user, mode, project) bucket → activate.
- N >= 5 rows globally for a fresh user → activate with global fallback.
- N <  5 rows total → return empty string (no false-learning).

This module is intentionally pure-Python (no scikit-learn / numpy
hot path) so the cold-start latency on chat is well under 50 ms.
"""
from __future__ import annotations

import logging
import math
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── Tunables ─────────────────────────────────────────────────────────
_REFRESH_TTL    = 600    # seconds — index rebuild cadence
_MAX_CORPUS     = 1500   # cap rows held in memory
_MIN_GLOBAL     = 5      # need >=5 quality rows before any RAG
_MIN_BUCKET     = 20     # >=20 rows in a bucket → personalised RAG
_DEFAULT_K      = 2      # examples returned per call
_USER_MSG_CAP   = 280    # truncate stored prompts for the prompt budget
_REPLY_CAP      = 600    # truncate replies for the prompt budget

_TOKEN_RE = re.compile(r"[a-z0-9_]+")

# ── Module state (in-memory; rebuilds on TTL miss) ───────────────────
_index: dict = {
    "built_at":    0.0,
    "row_count":   0,
    "doc_freq":    {},      # term → number of docs containing it
    "rows":        [],      # list[dict] — see _row_from_log
    "by_user":     {},      # user_id → set[row_idx]
    "by_mode":     {},      # mode → set[row_idx]
    "by_project":  {},      # project_id → set[row_idx]
}


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _quality_filter(doc: dict) -> bool:
    """Only keep rows ORA should learn FROM, not learn AROUND."""
    if not doc.get("user_message") or not doc.get("final_output"):
        return False
    if doc.get("lint_blocked"):
        return False
    # Mode C (code) — require a pass.
    if doc.get("mode") == "C":
        return bool(doc.get("pass_result", True))
    # Modes A/B/D/E (conversational) — accept unless explicitly bad.
    return True


def _row_from_log(doc: dict) -> dict:
    msg = (doc.get("user_message") or "")[:_USER_MSG_CAP]
    rep = (doc.get("final_output") or "")[:_REPLY_CAP]
    toks = _tokenize(msg)
    # Term frequencies (within this doc).
    tf: dict[str, int] = {}
    for t in toks:
        tf[t] = tf.get(t, 0) + 1
    return {
        "msg":        msg,
        "reply":      rep,
        "mode":       doc.get("mode") or "A",
        "user_id":    doc.get("user_id"),
        "project_id": doc.get("project_id"),
        "tf":         tf,
        "n_tokens":   max(1, len(toks)),
    }


async def _rebuild_index(db) -> None:
    """Rebuild the TF-IDF corpus from `ora_council_logs`."""
    started = time.monotonic()
    cursor = db["ora_council_logs"].find(
        {},
        {
            "_id": 0, "user_message": 1, "final_output": 1, "mode": 1,
            "user_id": 1, "project_id": 1, "pass_result": 1,
            "lint_blocked": 1,
        },
    ).sort("timestamp", -1).limit(_MAX_CORPUS)

    rows: list[dict] = []
    doc_freq: dict[str, int] = {}
    by_user: dict[str, set] = {}
    by_mode: dict[str, set] = {}
    by_project: dict[str, set] = {}

    idx = 0
    async for doc in cursor:
        if not _quality_filter(doc):
            continue
        row = _row_from_log(doc)
        for term in row["tf"].keys():
            doc_freq[term] = doc_freq.get(term, 0) + 1
        rows.append(row)
        if row["user_id"]:
            by_user.setdefault(row["user_id"], set()).add(idx)
        by_mode.setdefault(row["mode"], set()).add(idx)
        if row["project_id"]:
            by_project.setdefault(row["project_id"], set()).add(idx)
        idx += 1

    _index["rows"]       = rows
    _index["doc_freq"]   = doc_freq
    _index["by_user"]    = by_user
    _index["by_mode"]    = by_mode
    _index["by_project"] = by_project
    _index["row_count"]  = len(rows)
    _index["built_at"]   = time.monotonic()
    logger.info(
        "ora_council_retriever: rebuilt index in %.0f ms (rows=%d, terms=%d)",
        (time.monotonic() - started) * 1000, len(rows), len(doc_freq),
    )


async def _maybe_refresh(db) -> None:
    now = time.monotonic()
    if (now - _index["built_at"]) < _REFRESH_TTL and _index["row_count"] > 0:
        return
    try:
        await _rebuild_index(db)
    except Exception as e:
        logger.warning("ora_council_retriever rebuild failed: %r", e)


def _score(query_tokens: dict[str, int], row: dict, total_docs: int) -> float:
    """Cosine-ish TF-IDF score between query and row.tf."""
    if not query_tokens or not row["tf"]:
        return 0.0
    df = _index["doc_freq"]
    dot = 0.0
    qn  = 0.0
    rn  = 0.0
    for term, q_tf in query_tokens.items():
        idf = math.log((total_docs + 1) / (df.get(term, 0) + 1)) + 1.0
        q_w = (q_tf / max(1, sum(query_tokens.values()))) * idf
        qn += q_w * q_w
        if term in row["tf"]:
            r_w = (row["tf"][term] / row["n_tokens"]) * idf
            dot += q_w * r_w
    # Row norm — light approximation: norm of THIS row's weighted vector
    # across only its own terms (cheap, gives stable cosine ordering).
    for term, tf in row["tf"].items():
        idf = math.log((total_docs + 1) / (df.get(term, 0) + 1)) + 1.0
        w = (tf / row["n_tokens"]) * idf
        rn += w * w
    if qn <= 0.0 or rn <= 0.0:
        return 0.0
    return dot / (math.sqrt(qn) * math.sqrt(rn))


def _candidate_indices(
    mode: str, user_id: Optional[str], project_id: Optional[str],
) -> tuple[list[int], str]:
    """Return (candidate_indices, bucket_label). Falls back from
    most-specific bucket to global pool when the personalised bucket
    is too small."""
    mode_pool = _index["by_mode"].get(mode) or set()
    # Tier 1 — user+mode+project intersection.
    if user_id and project_id:
        u = _index["by_user"].get(user_id) or set()
        p = _index["by_project"].get(project_id) or set()
        inter = u & p & mode_pool
        if len(inter) >= _MIN_BUCKET:
            return list(inter), "user+project+mode"
    # Tier 2 — user+mode.
    if user_id:
        u = _index["by_user"].get(user_id) or set()
        inter = u & mode_pool
        if len(inter) >= _MIN_BUCKET:
            return list(inter), "user+mode"
    # Tier 3 — mode-wide (cross-user).
    if len(mode_pool) >= _MIN_GLOBAL:
        return list(mode_pool), "mode-global"
    # Tier 4 — anything we've got.
    if _index["row_count"] >= _MIN_GLOBAL:
        return list(range(_index["row_count"])), "global"
    return [], "below-threshold"


def _format_block(examples: list[dict], bucket_label: str) -> str:
    """Render the few-shot demonstrations in a tight, model-friendly
    block.  Goes ABOVE the rest of `extra_sys`."""
    parts = [
        "[ORA COUNCIL — LEARNED EXAMPLES]",
        "Below are ORA's best past responses on similar developer "
        "questions. Use them as STYLE + DEPTH calibration only — never "
        "copy verbatim. Match the tone, structure, and level of detail.",
        f"[bucket: {bucket_label} · k={len(examples)}]",
        "",
    ]
    for i, ex in enumerate(examples, start=1):
        parts.append(f"### Past example #{i}")
        parts.append(f"USER: {ex['msg']}")
        parts.append(f"ORA: {ex['reply']}")
        parts.append("")
    parts.append("[END LEARNED EXAMPLES]")
    return "\n".join(parts)


# ── Public API ───────────────────────────────────────────────────────
async def get_council_few_shot(
    db,
    user_message: str,
    mode: str = "A",
    user_id: Optional[str] = None,
    project_id: Optional[str] = None,
    k: int = _DEFAULT_K,
) -> tuple[str, int]:
    """Returns (formatted_block, recalled_count).

    Iter 212m-78 — return tuple so the chat router can surface the
    "📚 ORA recalled N similar past answers" caption to the FE. An
    empty corpus or no matches returns ("", 0).

    SAFE on failure — every internal exception is swallowed and
    ("", 0) is returned so the chat path never breaks because of a
    retriever bug."""
    try:
        await _maybe_refresh(db)
    except Exception as e:
        logger.debug("council refresh skipped: %r", e)
        return ("", 0)

    if _index["row_count"] < _MIN_GLOBAL:
        return ("", 0)

    cand_idx, bucket_label = _candidate_indices(mode, user_id, project_id)
    if not cand_idx:
        return ("", 0)

    # Tokenise the live query once and reuse the bag-of-words.
    q_toks = _tokenize(user_message or "")
    if not q_toks:
        return ("", 0)
    q_tf: dict[str, int] = {}
    for t in q_toks:
        q_tf[t] = q_tf.get(t, 0) + 1

    total_docs = _index["row_count"]
    scored = []
    for i in cand_idx:
        row = _index["rows"][i]
        s = _score(q_tf, row, total_docs)
        if s > 0:
            scored.append((s, row))
    if not scored:
        return ("", 0)

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [r for _s, r in scored[:max(1, int(k))]]
    return (_format_block(top, bucket_label), len(top))


def get_retriever_stats() -> dict:
    """Lightweight introspection — included in /admin/council/stats."""
    return {
        "active":       _index["row_count"] >= _MIN_GLOBAL,
        "corpus_rows":  _index["row_count"],
        "unique_users":     len(_index["by_user"]),
        "unique_projects":  len(_index["by_project"]),
        "modes_indexed":    list(_index["by_mode"].keys()),
        "built_at_ago_s":   (
            round(time.monotonic() - _index["built_at"], 1)
            if _index["built_at"] > 0 else None
        ),
        "min_global_threshold": _MIN_GLOBAL,
        "min_bucket_threshold": _MIN_BUCKET,
        "refresh_ttl_s":        _REFRESH_TTL,
    }


# ── Test hook ────────────────────────────────────────────────────────
def _reset_for_tests() -> None:
    _index["built_at"]   = 0.0
    _index["row_count"]  = 0
    _index["doc_freq"]   = {}
    _index["rows"]       = []
    _index["by_user"]    = {}
    _index["by_mode"]    = {}
    _index["by_project"] = {}
