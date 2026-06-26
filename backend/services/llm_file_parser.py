"""
services/llm_file_parser.py — Tolerant `FILE: <path> ... ``` ... ```` block parser.

The Swift / Pro / Maxx code-edit pipeline previously used one rigid regex
to extract file edits from the LLM reply:

    re.finditer(r"FILE:\\s*(\\S+)\\s*\\n```[^\\n]*\\n(.*?)```", reply, re.DOTALL)

That regex silently dropped real edits when the model returned:
  • `FILE :path` or `file: path`             (extra space / lowercase)
  • Four-or-more backticks around the body   (some Claude turns)
  • Missing newline after the language tag   (`\\`\\`\\`py<code>`)
  • Trailing whitespace before the closing fence
  • Closing fence as `\\`\\`\\`\\``  (four)  (some GLM turns)

`parse_file_blocks(reply)` returns the same `{path: content}` dict but
tolerates all of the above. It's intentionally a small, deterministic
helper — no LLM round-trips, no fuzzy matching across files, and we
prefer false negatives over false positives (an over-greedy match could
swallow the next file's header).
"""
from __future__ import annotations

import re
from typing import Iterator

# ── Patterns ─────────────────────────────────────────────────────────
# `FILE:` (case-insensitive, leading whitespace tolerated, `FILE :` ok),
# the path (no whitespace), optional trailing spaces, then a newline.
_HEADER_RE = re.compile(
    r"^[ \t]*FILE[ \t]*:[ \t]*(\S+)[ \t]*\r?\n",
    re.MULTILINE | re.IGNORECASE,
)

# A fence is 3-or-more backticks (or tildes) optionally followed by a
# language tag on the same line, then a newline. The number of fence
# characters is captured so the closing fence can match the SAME count
# (or more) — same rule CommonMark uses.
_FENCE_OPEN_RE = re.compile(
    r"\A(`{3,}|~{3,})[ \t]*([\w+\-]*)[ \t]*\r?\n",
)


def parse_file_blocks(reply: str) -> dict[str, str]:
    """Return `{path: body}` for every `FILE: …\\n```\\n…\\n```` block.

    Multiple edits to the same path keep the LAST occurrence (matches
    the legacy `dict[m.group(1)] = m.group(2)` overwrite semantics).
    """
    out: dict[str, str] = {}
    for path, body in _iter_blocks(reply or ""):
        out[path.strip()] = body
    return out


def _iter_blocks(reply: str) -> Iterator[tuple[str, str]]:
    """Generator — yields one `(path, body)` per detected block."""
    cursor = 0
    while True:
        m = _HEADER_RE.search(reply, cursor)
        if not m:
            return
        path = m.group(1)
        # Right after the header line, expect an opening fence.
        after_header = reply[m.end():]
        fence_m = _FENCE_OPEN_RE.match(after_header)
        if not fence_m:
            # Not a real block — skip past the header and keep looking.
            cursor = m.end()
            continue
        fence = fence_m.group(1)              # 3+ backticks/tildes
        fence_char = fence[0]
        fence_len = len(fence)

        body_start = m.end() + fence_m.end()
        # Closing fence: same char, at least fence_len long, on its own
        # line. We capture the body INCLUDING the trailing newline that
        # immediately precedes the closing fence — that matches the
        # legacy regex semantics so downstream byte-for-byte equality
        # checks (e.g. "unchanged file" detection) keep working.
        close_re = re.compile(
            r"(\r?\n)" + re.escape(fence_char) + r"{" + str(fence_len) +
            r",}[ \t]*(?:\r?\n|\Z)",
        )
        close_m = close_re.search(reply, body_start)
        if not close_m:
            # Unterminated block — bail rather than swallow the rest of
            # the reply. The auto-retry path will surface this as an
            # empty `edits` and re-prompt the model.
            cursor = m.end()
            continue
        # body_end = end of the preceding newline (inclusive), so the
        # captured text always carries its terminating "\n".
        body_end = close_m.start() + len(close_m.group(1))
        body = reply[body_start: body_end]
        # The legacy regex captured a leading newline-less body; the
        # tolerant parser does the same (no `body.lstrip()`).
        yield path, body
        cursor = close_m.end()
