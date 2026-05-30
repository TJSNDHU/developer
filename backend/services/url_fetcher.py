"""
services/url_fetcher.py — When a user pastes URLs into a chat prompt, fetch
them server-side and inject the cleaned page text back into the LLM system
context. Same idea as repo_context.py but for arbitrary public URLs.

We intentionally keep this simple:
  - URLs are extracted with a permissive regex from the raw prompt
  - Up to MAX_URLS are fetched in parallel
  - HTML is stripped to readable text via BeautifulSoup
  - Non-HTML responses (JSON, plain text, markdown, raw code) pass through
  - Each page is capped at MAX_CHARS_PER_URL; combined budget MAX_TOTAL_CHARS
  - Failures degrade gracefully — one bad URL won't break the others

Security: we fetch with a 10s timeout and refuse private/loopback hosts so
the bot can't be used to scan internal infra. SSRF guard is intentionally
conservative.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# ── Tunables ─────────────────────────────────────────────────────────────
MAX_URLS = 5
MAX_CHARS_PER_URL = 6000
MAX_TOTAL_CHARS = 20000
TIMEOUT_SECONDS = 10.0
USER_AGENT = "AuremCTO/1.0 (+https://auremcto.com)"

# Permissive URL regex — catches http(s) URLs with optional path/query/anchor.
# We don't try to be RFC-perfect; downstream urlparse + fetch will fail-soft.
_URL_RE = re.compile(
    r"https?://[^\s<>\"'\)\]\}]+",
    re.IGNORECASE,
)


def extract_urls(text: str) -> list[str]:
    """Pull out unique URLs from a user prompt, preserving first-seen order."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _URL_RE.finditer(text):
        u = m.group(0).rstrip(".,;:)")  # strip common trailing punctuation
        if u not in seen:
            seen.add(u)
            out.append(u)
        if len(out) >= MAX_URLS:
            break
    return out


def _is_safe_host(host: str) -> bool:
    """Reject loopback / private / link-local / metadata hosts to block SSRF."""
    if not host:
        return False
    low = host.lower()
    if low in ("localhost", "metadata.google.internal"):
        return False
    try:
        # If it's already a literal IP, validate directly.
        ip = ipaddress.ip_address(host)
        return not (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast
        )
    except ValueError:
        pass
    # DNS-resolve the hostname and reject if any resolved IP is private.
    try:
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast):
                return False
    except Exception:
        # If we can't resolve, the request will fail anyway — let httpx handle it
        return True
    return True


def _strip_html(html: str) -> str:
    """Convert HTML → readable plain text. Drop scripts/styles/nav noise."""
    try:
        from bs4 import BeautifulSoup
    except Exception:
        # bs4 not available → very crude tag stripper
        cleaned = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
        cleaned = re.sub(r"<style[\s\S]*?</style>", " ", cleaned, flags=re.I)
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "svg",
                      "nav", "footer", "header", "form"]):
        tag.decompose()
    # Prefer <main>/<article> when present so we skip nav chrome
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = main.get_text("\n", strip=True)
    # Collapse runs of blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def _fetch_one(url: str) -> dict:
    """Fetch a single URL and return {url, ok, title, content, error}."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return {"url": url, "ok": False, "error": "unsupported scheme"}
        if not _is_safe_host(parsed.hostname or ""):
            return {"url": url, "ok": False, "error": "blocked host"}

        async with httpx.AsyncClient(
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        ) as client:
            r = await client.get(url)
            if r.status_code >= 400:
                return {"url": url, "ok": False,
                        "error": f"HTTP {r.status_code}"}
            ctype = (r.headers.get("content-type") or "").lower()
            raw = r.text or ""

            # HTML → strip; everything else (json/markdown/plain/code) raw
            if "html" in ctype:
                content = _strip_html(raw)
            else:
                content = raw

            # Try to extract a title for the page (HTML only)
            title = ""
            if "html" in ctype:
                m = re.search(r"<title[^>]*>(.*?)</title>",
                              raw, re.I | re.S)
                if m:
                    title = re.sub(r"\s+", " ", m.group(1)).strip()[:120]

            if len(content) > MAX_CHARS_PER_URL:
                content = content[:MAX_CHARS_PER_URL] + "\n... [truncated]"
            return {"url": url, "ok": True,
                    "title": title, "content": content}
    except httpx.TimeoutException:
        return {"url": url, "ok": False, "error": "timeout"}
    except Exception as e:
        logger.debug(f"url fetch failed for {url}: {e!r}")
        return {"url": url, "ok": False, "error": type(e).__name__}


async def build_url_context(prompt: str) -> str:
    """If the prompt contains URLs, fetch them and return a system-prompt
    blob containing their cleaned contents. Returns "" when no URLs."""
    urls = extract_urls(prompt)
    if not urls:
        return ""

    results = await asyncio.gather(*[_fetch_one(u) for u in urls])

    parts: list[str] = ["=== FETCHED URL CONTENT ==="]
    parts.append(
        "The user pasted the following URLs. The orchestrator already "
        "fetched them — use the actual content below to answer. Never "
        "tell the user you can't access URLs."
    )
    parts.append("")

    used = 0
    for r in results:
        url = r.get("url", "?")
        if not r.get("ok"):
            parts.append(f"--- {url} ---")
            parts.append(f"(could not fetch: {r.get('error', 'unknown')})")
            parts.append("")
            continue
        title = r.get("title") or ""
        content = r.get("content") or ""
        # Enforce global budget
        if used + len(content) > MAX_TOTAL_CHARS:
            remaining = max(0, MAX_TOTAL_CHARS - used)
            if remaining < 500:
                parts.append(f"--- {url} ---")
                parts.append("(omitted — total URL budget exceeded)")
                parts.append("")
                continue
            content = content[:remaining] + "\n... [truncated by global budget]"
        used += len(content)
        parts.append(f"--- {url} ---")
        if title:
            parts.append(f"title: {title}")
        parts.append(content)
        parts.append("")

    parts.append("=== END FETCHED URL CONTENT ===")
    return "\n".join(parts)
