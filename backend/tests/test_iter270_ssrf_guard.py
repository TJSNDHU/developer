"""Iter 270 — SSRF hard gate on `_fetch_one_url`.

Asserts every private / loopback / link-local / metadata / non-http
scheme is rejected BEFORE any network I/O, and that hostile redirects
to those targets are also blocked at the redirect-hop level.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ora_chat.deep_research import (
    _fetch_one_url,
    _is_safe_public_url,
    _ip_is_public,
)


# ─── Unit: IP classifier ───────────────────────────────────────────
@pytest.mark.parametrize("ip,ok", [
    ("8.8.8.8", True),
    ("1.1.1.1", True),
    ("2606:4700:4700::1111", True),           # public IPv6 (Cloudflare)
    ("169.254.169.254", False),               # AWS metadata
    ("127.0.0.1", False),                     # loopback
    ("10.0.0.1", False),                      # RFC1918
    ("192.168.1.1", False),
    ("172.16.0.1", False),
    ("100.64.0.1", False),                    # CGNAT
    ("0.0.0.0", False),                       # unspecified
    ("224.0.0.1", False),                     # multicast
    ("::1", False),                           # IPv6 loopback
    ("fe80::1", False),                       # IPv6 link-local
    ("fc00::1", False),                       # IPv6 unique-local
])
def test_ip_is_public(ip, ok):
    assert _ip_is_public(ip)[0] is ok, (ip, _ip_is_public(ip))


# ─── URL gate: bare IPs & schemes ──────────────────────────────────
@pytest.mark.parametrize("url,ok_expected", [
    ("http://169.254.169.254/latest/meta-data/", False),
    ("http://127.0.0.1:8001/api/admin", False),
    ("http://10.0.0.5/", False),
    ("http://[::1]/", False),
    ("http://localhost/", False),
    ("http://LocalHost/", False),
    ("file:///etc/passwd", False),
    ("gopher://evil/", False),
    ("ftp://internal/", False),
    ("javascript:alert(1)", False),
    ("data:text/html,<script>", False),
    ("://no-scheme.example", False),
    ("http://", False),
    ("https://8.8.8.8/", True),               # public IP direct is OK
])
def test_is_safe_public_url_direct(url, ok_expected):
    ok, why = _is_safe_public_url(url)
    assert ok is ok_expected, (url, ok, why)


# ─── URL gate: DNS resolution ──────────────────────────────────────
def test_hostname_resolving_to_private_is_blocked():
    """A hostile domain that resolves to 10.x is rejected pre-fetch."""
    fake_infos = [(2, 1, 6, "", ("10.0.0.5", 0))]
    with patch("services.ora_chat.deep_research.socket.getaddrinfo",
               return_value=fake_infos):
        ok, why = _is_safe_public_url("http://evil.example.com/")
    assert ok is False
    assert "dns_resolves_to_private" in why


def test_hostname_resolving_to_link_local_is_blocked():
    fake_infos = [(2, 1, 6, "", ("169.254.169.254", 0))]
    with patch("services.ora_chat.deep_research.socket.getaddrinfo",
               return_value=fake_infos):
        ok, why = _is_safe_public_url("http://cloud-metadata.example/")
    assert ok is False
    assert ("link_local" in why) or ("private" in why)


def test_hostname_resolving_to_public_is_allowed():
    fake_infos = [(2, 1, 6, "", ("8.8.8.8", 0))]
    with patch("services.ora_chat.deep_research.socket.getaddrinfo",
               return_value=fake_infos):
        ok, _ = _is_safe_public_url("https://dns.google/")
    assert ok is True


def test_multi_answer_dns_any_private_blocks():
    """If a name resolves to BOTH a public and a private IP, block —
    the attacker only needs one bad hop."""
    fake_infos = [
        (2, 1, 6, "", ("8.8.8.8", 0)),
        (2, 1, 6, "", ("10.0.0.1", 0)),
    ]
    with patch("services.ora_chat.deep_research.socket.getaddrinfo",
               return_value=fake_infos):
        ok, why = _is_safe_public_url("http://mixed.example/")
    assert ok is False
    assert "private" in why


# ─── Integration: _fetch_one_url short-circuits pre-network ────────
def test_fetch_one_url_blocks_metadata_no_network_call():
    """Metadata URL must be rejected *before* any HTTP call fires."""
    client = MagicMock()
    client.get = AsyncMock()   # will raise if invoked
    result = asyncio.run(_fetch_one_url(
        client, "http://169.254.169.254/latest/meta-data/"))
    assert result["ok"] is False
    assert result["error"].startswith("blocked_ssrf:")
    client.get.assert_not_awaited()


def test_fetch_one_url_blocks_file_scheme():
    client = MagicMock()
    client.get = AsyncMock()
    result = asyncio.run(_fetch_one_url(client, "file:///etc/passwd"))
    assert result["ok"] is False
    assert "scheme_file" in result["error"]
    client.get.assert_not_awaited()


# ─── Integration: redirect to a private IP is blocked mid-chain ────
def test_fetch_one_url_blocks_redirect_to_private():
    """Public URL that 302-redirects to 169.254.169.254 must be
    rejected on the second hop — the SSRF guard re-validates every
    hop.
    """
    from services.ora_chat import deep_research as dr

    hop1 = MagicMock()
    hop1.status_code = 302
    hop1.headers = {"location": "http://169.254.169.254/latest/meta-data/"}

    client = MagicMock()
    client.get = AsyncMock(return_value=hop1)

    # Stub the safe-URL check to accept hop-1 but block hop-2.
    real = dr._is_safe_public_url
    calls = {"n": 0}

    def stub(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return True, ""
        return real(url)

    # Also stub robots to allow.
    async def robots_ok(_c, _u):
        return True

    with patch.object(dr, "_is_safe_public_url", side_effect=stub), \
         patch.object(dr, "_robots_allows", side_effect=robots_ok):
        result = asyncio.run(dr._fetch_one_url(client, "https://safe.example/"))

    assert result["ok"] is False
    assert result["error"].startswith("blocked_ssrf_redirect:")


def test_fetch_one_url_bounded_redirects():
    """More than _MAX_REDIRECTS hops → too_many_redirects."""
    from services.ora_chat import deep_research as dr

    resp = MagicMock()
    resp.status_code = 302
    resp.headers = {"location": "https://safe.example/next"}

    client = MagicMock()
    client.get = AsyncMock(return_value=resp)

    async def robots_ok(_c, _u):
        return True

    with patch.object(dr, "_is_safe_public_url",
                      return_value=(True, "")), \
         patch.object(dr, "_robots_allows", side_effect=robots_ok):
        result = asyncio.run(dr._fetch_one_url(client, "https://safe.example/"))

    assert result["ok"] is False
    assert result["error"] == "too_many_redirects"


# ─── Regression: happy path still works ────────────────────────────
def test_fetch_one_url_happy_path_public():
    from services.ora_chat import deep_research as dr

    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "text/plain"}
    resp.text = "Hello, world. This is a valid public response body."

    client = MagicMock()
    client.get = AsyncMock(return_value=resp)

    async def robots_ok(_c, _u):
        return True

    with patch.object(dr, "_is_safe_public_url",
                      return_value=(True, "")), \
         patch.object(dr, "_robots_allows", side_effect=robots_ok):
        result = asyncio.run(dr._fetch_one_url(client, "https://safe.example/"))

    assert result["ok"] is True
    assert "Hello" in result["text"]
