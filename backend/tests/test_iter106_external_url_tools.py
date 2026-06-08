"""Iter 106 — persona must let ORA read EXTERNAL public URLs (e.g. a
GitHub repo the user pastes) using web tools, instead of falsely
refusing with 'I only work with your connected project'.

User-reported example:
    "can you take a look in this github repo and reverse engineer and
     show me how its working   github.com/companion-inc/feynman"
    → BAD reply: "I can't access external GitHub repos directly — I
                  only work with your connected project."

The web tools `fetch_url`, `web_search`, `firecrawl_scrape` are wired
and available; the model just needed an explicit rule that says: use
them on public URLs. Don't refuse.
"""
from services.orchestrator import AUREM_CTO_PERSONA


def test_external_urls_section_exists():
    assert "EXTERNAL URLS & PUBLIC REPOS — USE WEB TOOLS, DO NOT REFUSE" in AUREM_CTO_PERSONA


def test_section_names_web_tools_explicitly():
    p = AUREM_CTO_PERSONA
    assert "fetch_url" in p
    assert "web_search" in p
    assert "firecrawl_scrape" in p


def test_section_calls_out_github_raw_strategy():
    """Real reverse-engineering needs raw.githubusercontent.com hits."""
    p = AUREM_CTO_PERSONA
    assert "raw.githubusercontent.com" in p
    assert "Parallelise" in p or "parallelise" in p


def test_section_forbids_oauth_gating_for_public_repos():
    p = AUREM_CTO_PERSONA
    assert "Do NOT route the user to 'connect it via GitHub OAuth first'" in p


def test_never_section_forbids_refusing_public_urls():
    p = AUREM_CTO_PERSONA
    # The NEVER section must reinforce the rule
    assert "Refuse to look at a PUBLIC URL" in p
    assert "you have web" in p.lower()


def test_no_overcorrect_breaks_connected_repo_rule():
    """The new section must NOT remove the connected-repo rules
    (read_repo_file / list_repo_files etc remain authoritative for the
    user's own repo)."""
    p = AUREM_CTO_PERSONA
    assert "read_repo_file" in p
    assert "list_repo_files" in p
    assert "semantic_search_repo" in p
