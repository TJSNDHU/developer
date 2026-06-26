"""
Iter 212m-33 — Tolerant FILE-block parser + Projects-page founder pill.

Two concerns, one file (small surface area, two source contracts):

  1. `services.llm_file_parser.parse_file_blocks` — every fragility
     case the legacy single-line regex used to drop silently.

  2. Source pins that the cto_projects worker calls the new parser
     (not the old regex) on both the primary and auto-retry paths,
     and that `/projects` renders the slim founder offer pill.
"""
from __future__ import annotations


# ── 1. Parser — fragility coverage ───────────────────────────────────

def test_parser_handles_canonical_block():
    from services.llm_file_parser import parse_file_blocks
    src = (
        "Some preamble.\n"
        "FILE: src/app.py\n"
        "```python\n"
        "print('hi')\n"
        "```\n"
        "After.\n"
    )
    assert parse_file_blocks(src) == {"src/app.py": "print('hi')\n"}


def test_parser_handles_lowercase_and_spaced_header():
    from services.llm_file_parser import parse_file_blocks
    src = (
        "file : a.py\n"
        "```\n"
        "X = 1\n"
        "```\n"
        "FILE:  b.py\n"
        "```py\n"
        "Y = 2\n"
        "```\n"
    )
    out = parse_file_blocks(src)
    assert out == {"a.py": "X = 1\n", "b.py": "Y = 2\n"}


def test_parser_handles_four_backticks_and_six_backticks():
    """Some Claude / GLM turns wrap blocks in 4+ backticks when the
    body itself contains a 3-backtick markdown sample."""
    from services.llm_file_parser import parse_file_blocks
    src = (
        "FILE: README.md\n"
        "````md\n"
        "# Hi\n"
        "```python\n"
        "print()\n"
        "```\n"
        "````\n"
    )
    out = parse_file_blocks(src)
    assert "README.md" in out
    body = out["README.md"]
    assert "# Hi" in body and "print()" in body
    # The inner 3-backtick fence MUST survive intact.
    assert "```python" in body


def test_parser_handles_tilde_fences():
    from services.llm_file_parser import parse_file_blocks
    src = "FILE: x.py\n~~~\nprint(1)\n~~~\n"
    assert parse_file_blocks(src) == {"x.py": "print(1)\n"}


def test_parser_skips_header_without_a_fence():
    """A bare `FILE:` line with no following fence used to crash the
    old regex's optional follow-up. The tolerant parser silently
    skips it and continues scanning."""
    from services.llm_file_parser import parse_file_blocks
    src = (
        "FILE: only-a-header-no-block.py\n"
        "Some prose here.\n"
        "\n"
        "FILE: real.py\n"
        "```\n"
        "ok\n"
        "```\n"
    )
    assert parse_file_blocks(src) == {"real.py": "ok\n"}


def test_parser_bails_on_unterminated_block_instead_of_swallowing_rest():
    """If the model forgets the closing fence we must NOT consume the
    rest of the reply into one giant body — that's how production
    edits silently exploded into 30-KB diffs in the past."""
    from services.llm_file_parser import parse_file_blocks
    src = (
        "FILE: bad.py\n"
        "```\n"
        "incomplete contents...\n"
        "(no closing fence)\n"
    )
    # bad.py is dropped; no key in the result.
    assert "bad.py" not in parse_file_blocks(src)


def test_parser_multiple_edits_same_path_keeps_last():
    """Legacy regex used dict overwrite — same semantics here."""
    from services.llm_file_parser import parse_file_blocks
    src = (
        "FILE: a.py\n```\nv1\n```\n"
        "FILE: a.py\n```\nv2\n```\n"
    )
    assert parse_file_blocks(src) == {"a.py": "v2\n"}


def test_parser_accepts_no_language_tag():
    from services.llm_file_parser import parse_file_blocks
    src = "FILE: tiny.txt\n```\nhello\n```\n"
    assert parse_file_blocks(src) == {"tiny.txt": "hello\n"}


def test_parser_empty_or_garbage_inputs():
    from services.llm_file_parser import parse_file_blocks
    assert parse_file_blocks("") == {}
    assert parse_file_blocks(None) == {}      # type: ignore[arg-type]
    assert parse_file_blocks("no file headers at all") == {}


def test_parser_path_trim_strips_trailing_whitespace_in_header():
    from services.llm_file_parser import parse_file_blocks
    src = "FILE:   spaced.py   \n```\nx\n```\n"
    assert parse_file_blocks(src) == {"spaced.py": "x\n"}


def test_parser_closing_fence_with_trailing_whitespace():
    from services.llm_file_parser import parse_file_blocks
    src = "FILE: ws.py\n```\nbody\n```   \n"
    assert parse_file_blocks(src) == {"ws.py": "body\n"}


def test_parser_closing_fence_can_be_longer_than_opening():
    """Common Mark — the closing fence must have at least as many
    fence chars as the opening one. Asserting we honour this."""
    from services.llm_file_parser import parse_file_blocks
    src = "FILE: f.py\n```\nbody\n``````\n"
    assert parse_file_blocks(src) == {"f.py": "body\n"}


# ── 2. Source pins — cto_projects worker + Projects page ─────────────

def test_cto_projects_uses_tolerant_parser():
    src = open("/app/backend/routers/cto_projects.py").read()
    # Both call sites must route through the new helper.
    assert src.count("from services.llm_file_parser import parse_file_blocks") >= 2
    assert "parse_file_blocks(reply)" in src
    assert "parse_file_blocks(reply2)" in src
    # The brittle regex used to live here; make sure neither call site
    # still uses it.
    assert "FILE:\\s*(\\S+)\\s*\\n```[^\\n]*\\n(.*?)```" not in src


def test_projects_page_renders_founder_offer_pill():
    src = open("/app/frontend/src/pages/Projects.jsx").read()
    assert "import FounderOfferPill from" in src
    assert "right={<FounderOfferPill />}" in src


def test_founder_offer_pill_polls_status_and_links_to_dashboard():
    src = open("/app/frontend/src/components/FounderOfferPill.jsx").read()
    assert '"/founder-offer/status"' in src
    assert "/dashboard?action=connect-repo" in src
    assert "utm_source=projects_pill" in src
    # Auto-hide once the offer sells out.
    assert "(s.remaining ?? 0) <= 0)" in src
