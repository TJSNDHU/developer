"""
core/task_type.py — Iter 212m-178

Deterministic task_type inference from a raw prompt, used by the chat
endpoints to pick the right council when the client sends no explicit
task_type override:
    'write'    → Council C   (docs / copy / email / markdown authoring)
    'analysis' → Council B   (codebase analysis / summaries)
    None       → default     (Council A code path)

Kept in its own tiny module (no heavy imports) so routers can call it
without pulling the full council-routing stack. The council router
re-exports `infer_task_type` for backwards compatibility.
"""
from __future__ import annotations

import re
from typing import Optional

_WRITING_NOUNS = re.compile(
    r"\b(email|e-mail|blog|post|readme|readme\.md|contributing|"
    r"code[_ ]?of[_ ]?conduct|license|licence|changelog|newsletter|"
    r"announcement|article|tweet|press release|copywrit\w*|marketing copy|"
    r"documentation|docs|user guide|onboarding guide|faq|tutorial|guide|"
    r"blurb|caption|description|summary paragraph|cover letter|proposal)\b",
    re.I)
_WRITING_VERBS = re.compile(
    r"\b(write|draft|compose|reword|rewrite|rephrase|author|pen)\b", re.I)
_CODE_NOUNS = re.compile(
    r"\b(function|class|endpoint|bug|test|route|module|method|refactor|"
    r"lint|error|exception|api|migration|schema|regex|docstring|"
    r"variable|import|dependency|typescript|python)\b", re.I)
_ANALYSIS_PAT = re.compile(
    r"\b(analy[sz]e|analysis|summari[sz]e|summary|assess|evaluate|"
    r"insight|health of|codebase health|report on|breakdown of|review of)\b",
    re.I)
# A *.md / *.txt authoring target strongly implies a writing task
# (CODE_OF_CONDUCT.md, LICENSE, ARCHITECTURE.md …) even when the exact
# filename isn't in the noun list above.
_DOC_FILE = re.compile(r"\b[\w-]+\.(?:md|mdx|txt|rst)\b", re.I)


def infer_task_type(text: Optional[str]) -> Optional[str]:
    t = text or ""
    _has_write_verb = bool(_WRITING_VERBS.search(t))
    _has_code = bool(_CODE_NOUNS.search(t))
    if _WRITING_NOUNS.search(t) and (_has_write_verb or not _has_code):
        return "write"
    if _has_write_verb and _DOC_FILE.search(t) and not _has_code:
        return "write"
    if _ANALYSIS_PAT.search(t) and not _has_code:
        return "analysis"
    return None
