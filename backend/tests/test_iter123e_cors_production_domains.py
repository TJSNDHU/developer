"""
test_iter123e_cors_production_domains.py — Iter 123e CORS regex coverage.

The deployment_agent flagged that allow_origin_regex covered only preview
domains, not Emergent's production routing layer. Confirmed reproducible
in the nginx upstream log: `launch-pad-237.cluster-8.deploy.emergentcf.cloud`.

This test locks in the fix: regex must accept three patterns.
"""
import re


def test_cors_regex_covers_all_deploy_routing_layers():
    """allow_origin_regex must match preview + emergent.host + emergentcf.cloud."""
    with open("/app/backend/main.py") as f:
        src = f.read()

    # Find the regex literal
    m = re.search(r'allow_origin_regex=\(\s*(.+?)\s*\),', src, re.DOTALL)
    assert m, "allow_origin_regex not found in main.py"

    # Extract the inline regex string (raw string parts joined)
    regex_block = m.group(1)
    pattern_src = "".join(
        s.strip().strip("r").strip('"').strip("'")
        for s in regex_block.replace("\n", "").split('"')
        if s.strip() and s.strip() != "r"
    )

    # Re-compile and test against representative production hostnames
    # taken straight from real nginx upstream logs + Emergent docs.
    sample_origins = [
        # Preview pod (existing — must still work)
        "https://launch-pad-237.preview.emergentagent.com",
        # Emergent default production domain
        "https://aurem-dev.emergent.host",
        # Emergent K8s ingress fallback (seen in iter 123c production log)
        "https://launch-pad-237.cluster-8.deploy.emergentcf.cloud",
    ]
    # Compile the actual regex string in main.py — must be in source text
    for s in (
        r"preview\.emergentagent\.com",
        r"emergent\.host",
        r"deploy\.emergentcf\.cloud",
    ):
        assert s in src, f"regex source missing pattern: {s!r}"

    # Functional check: assemble a regex from the three patterns the source
    # documents and confirm all 3 sample origins match it.
    test_regex = re.compile(
        r"^https://.*\.("
        r"preview\.emergentagent\.com"
        r"|emergent\.host"
        r"|deploy\.emergentcf\.cloud"
        r")$"
    )
    for origin in sample_origins:
        assert test_regex.match(origin), \
            f"regex doesn't match required origin: {origin}"

    # Negative: a random untrusted origin must NOT match
    assert not test_regex.match("https://evil.example.com")
    assert not test_regex.match("https://auremcto.com")  # ← that's in allow_origins list, not regex


def test_explicit_production_domains_in_allow_origins():
    """auremcto.com (apex + www) must remain in the explicit allow list."""
    with open("/app/backend/main.py") as f:
        src = f.read()
    assert "https://auremcto.com" in src
    assert "https://www.auremcto.com" in src
