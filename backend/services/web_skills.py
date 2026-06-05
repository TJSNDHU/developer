"""
services/web_skills.py — Web skills for ORA.

Closes the "ORA has no internet" gap. Five skills wired into the
orchestrator's tool-call layer (see services/local_tools.py):

  • web_search                  Tavily /search — Google-style top-N results
  • fetch_url                   Tavily /extract — clean text for any URL
  • web_search_and_summarize    /search with include_answer=True (Tavily's
                                own 1-line summary across the result set)
  • firecrawl_scrape            Firecrawl /v1/scrape — JS-heavy pages,
                                PDFs, screenshots
  • firecrawl_crawl_site        Firecrawl /v1/crawl — full domain crawl
                                (async + polled)

All keys live in os.environ. Missing key → clean `{"ok": False,
"error": "..."}` (never crash, never raise into the orchestrator).
Pure httpx — no new SDKs added.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

TAVILY_BASE = "https://api.tavily.com"
FIRECRAWL_BASE = "https://api.firecrawl.dev/v1"

TAVILY_TIMEOUT = 15.0     # per playbook hard cap
FIRECRAWL_SCRAPE_TIMEOUT = 60.0
FIRECRAWL_CRAWL_POLL_MAX = 90.0   # total seconds we'll wait for /crawl

URL_RE = re.compile(r"https?://[^\s\"'<>]+")


# ── SSRF guard reused from services/url_fetcher.py ────────────────────
_BLOCKED_HOSTS = {"localhost", "0.0.0.0", "127.0.0.1", "::1"}
_BLOCKED_PREFIXES = (
    "10.", "192.168.", "169.254.",
    "172.16.", "172.17.", "172.18.", "172.19.", "172.20.",
    "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
    "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.",
)


def _is_blocked(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return True
    if not host:
        return True
    if host in _BLOCKED_HOSTS:
        return True
    return any(host.startswith(p) for p in _BLOCKED_PREFIXES)


def _tavily_key() -> str | None:
    return (os.environ.get("TAVILY_API_KEY") or "").strip() or None


def _firecrawl_key() -> str | None:
    return (os.environ.get("FIRECRAWL_API_KEY") or "").strip() or None


# ── 1. web_search (Tavily /search) ────────────────────────────────────
async def web_search(ctx: dict, args: dict) -> dict:
    """Top-N Google-style results for a query."""
    key = _tavily_key()
    if not key:
        return {"ok": False, "error": "TAVILY_API_KEY not configured on server"}
    query = (args or {}).get("query", "").strip()
    if not query:
        return {"ok": False, "error": "Missing required arg `query`"}
    max_results = int((args or {}).get("max_results") or 5)
    max_results = max(1, min(max_results, 10))
    search_depth = "advanced" if (args or {}).get("deep") else "basic"

    payload = {
        "query": query[:380],
        "search_depth": search_depth,
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "topic": (args or {}).get("topic") or "general",
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=TAVILY_TIMEOUT) as c:
            r = await c.post(f"{TAVILY_BASE}/search", json=payload, headers=headers)
    except httpx.TimeoutException:
        return {"ok": False, "error": "Tavily search timed out after 15s"}
    except httpx.RequestError as e:
        return {"ok": False, "error": f"Tavily search network error: {e}"}

    if r.status_code == 429:
        return {"ok": False, "error": "Tavily rate-limited (429)",
                "retry_after": r.headers.get("retry-after")}
    if r.status_code >= 400:
        return {"ok": False, "error": f"Tavily {r.status_code}: {r.text[:300]}"}

    data = r.json() or {}
    results = []
    for row in (data.get("results") or [])[:max_results]:
        results.append({
            "title": row.get("title") or "",
            "url":   row.get("url") or "",
            "snippet": (row.get("content") or "")[:600],
            "score": row.get("score"),
        })
    return {
        "ok": True,
        "query": query,
        "results": results,
        "count": len(results),
        "depth": search_depth,
    }


# ── 2. fetch_url (Tavily /extract) ────────────────────────────────────
async def fetch_url(ctx: dict, args: dict) -> dict:
    """Clean text content for a single URL (or up to 5)."""
    key = _tavily_key()
    if not key:
        return {"ok": False, "error": "TAVILY_API_KEY not configured on server"}

    raw_urls = (args or {}).get("urls") or (args or {}).get("url")
    if isinstance(raw_urls, str):
        raw_urls = [raw_urls]
    if not isinstance(raw_urls, list) or not raw_urls:
        return {"ok": False, "error": "Missing required arg `url` or `urls`"}

    urls = []
    for u in raw_urls[:5]:
        if not isinstance(u, str) or not u.startswith(("http://", "https://")):
            continue
        if _is_blocked(u):
            continue
        urls.append(u)
    if not urls:
        return {"ok": False, "error": "No safe public URLs given"}

    payload = {
        "urls": urls,
        "extract_depth": "advanced" if (args or {}).get("deep") else "basic",
        "format": "markdown",
        "include_images": False,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=TAVILY_TIMEOUT) as c:
            r = await c.post(f"{TAVILY_BASE}/extract", json=payload, headers=headers)
    except httpx.TimeoutException:
        return {"ok": False, "error": "Tavily extract timed out after 15s"}
    except httpx.RequestError as e:
        return {"ok": False, "error": f"Tavily extract network error: {e}"}

    if r.status_code == 429:
        return {"ok": False, "error": "Tavily rate-limited (429)"}
    if r.status_code >= 400:
        return {"ok": False, "error": f"Tavily {r.status_code}: {r.text[:300]}"}

    data = r.json() or {}
    out = []
    for row in (data.get("results") or []):
        content = (row.get("raw_content") or row.get("content") or "")
        out.append({
            "url":   row.get("url"),
            "title": row.get("title") or "",
            "content": content[:8000],   # hard cap so the LLM context is safe
            "truncated": len(content) > 8000,
        })
    failed = data.get("failed_results") or []
    return {"ok": True, "results": out, "failed": failed, "count": len(out)}


# ── 3. web_search_and_summarize (Tavily /search w/ include_answer) ────
async def web_search_and_summarize(ctx: dict, args: dict) -> dict:
    """Search the web AND get Tavily's own 1-paragraph answer."""
    key = _tavily_key()
    if not key:
        return {"ok": False, "error": "TAVILY_API_KEY not configured on server"}
    query = (args or {}).get("query", "").strip()
    if not query:
        return {"ok": False, "error": "Missing required arg `query`"}
    max_results = int((args or {}).get("max_results") or 5)
    max_results = max(1, min(max_results, 8))

    payload = {
        "query": query[:380],
        "search_depth": "advanced" if (args or {}).get("deep") else "basic",
        "max_results": max_results,
        "include_answer": True,
        "include_raw_content": False,
        "include_images": False,
        "topic": (args or {}).get("topic") or "general",
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=TAVILY_TIMEOUT) as c:
            r = await c.post(f"{TAVILY_BASE}/search", json=payload, headers=headers)
    except httpx.TimeoutException:
        return {"ok": False, "error": "Tavily summarize timed out after 15s"}
    except httpx.RequestError as e:
        return {"ok": False, "error": f"Tavily summarize network error: {e}"}

    if r.status_code == 429:
        return {"ok": False, "error": "Tavily rate-limited (429)"}
    if r.status_code >= 400:
        return {"ok": False, "error": f"Tavily {r.status_code}: {r.text[:300]}"}

    data = r.json() or {}
    citations = [{
        "title": row.get("title") or "",
        "url":   row.get("url") or "",
        "snippet": (row.get("content") or "")[:300],
    } for row in (data.get("results") or [])[:max_results]]

    return {
        "ok": True,
        "query": query,
        "answer": (data.get("answer") or "").strip(),
        "citations": citations,
        "count": len(citations),
    }


# ── 4. firecrawl_scrape (Firecrawl /v1/scrape) ────────────────────────
async def firecrawl_scrape(ctx: dict, args: dict) -> dict:
    """JS-rendered scrape of a single URL (Firecrawl)."""
    key = _firecrawl_key()
    if not key:
        return {"ok": False,
                "error": "FIRECRAWL_API_KEY not configured on server. "
                         "Add it to backend/.env to enable JS-heavy scraping."}
    url = (args or {}).get("url", "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "Missing or invalid `url`"}
    if _is_blocked(url):
        return {"ok": False, "error": "URL refused by SSRF guard"}

    formats = (args or {}).get("formats") or ["markdown"]
    if isinstance(formats, str):
        formats = [formats]
    formats = [f for f in formats if f in ("markdown", "html", "screenshot", "links")][:3]
    if not formats:
        formats = ["markdown"]

    payload: dict[str, Any] = {"url": url, "formats": formats}
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=FIRECRAWL_SCRAPE_TIMEOUT) as c:
            r = await c.post(f"{FIRECRAWL_BASE}/scrape", json=payload, headers=headers)
    except httpx.TimeoutException:
        return {"ok": False, "error": "Firecrawl scrape timed out after 60s"}
    except httpx.RequestError as e:
        return {"ok": False, "error": f"Firecrawl scrape network error: {e}"}

    if r.status_code == 429:
        return {"ok": False, "error": "Firecrawl rate-limited (429)"}
    if r.status_code >= 400:
        return {"ok": False, "error": f"Firecrawl {r.status_code}: {r.text[:300]}"}

    payload_out = r.json() or {}
    data = payload_out.get("data") or {}
    md = (data.get("markdown") or "")
    return {
        "ok": bool(payload_out.get("success", True)),
        "url": url,
        "title": (data.get("metadata") or {}).get("title") or "",
        "markdown": md[:12_000],
        "truncated": len(md) > 12_000,
        "screenshot_url": data.get("screenshot"),
        "links": (data.get("links") or [])[:50],
    }


# ── 5. firecrawl_crawl_site (Firecrawl /v1/crawl async) ───────────────
async def firecrawl_crawl_site(ctx: dict, args: dict) -> dict:
    """Crawl up to N pages from a domain (Firecrawl)."""
    key = _firecrawl_key()
    if not key:
        return {"ok": False,
                "error": "FIRECRAWL_API_KEY not configured on server. "
                         "Add it to backend/.env to enable site crawling."}
    url = (args or {}).get("url", "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "Missing or invalid `url`"}
    if _is_blocked(url):
        return {"ok": False, "error": "URL refused by SSRF guard"}

    limit = int((args or {}).get("limit") or 10)
    limit = max(1, min(limit, 50))

    payload = {
        "url": url,
        "limit": limit,
        "scrapeOptions": {"formats": ["markdown"]},
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{FIRECRAWL_BASE}/crawl", json=payload, headers=headers)
    except httpx.RequestError as e:
        return {"ok": False, "error": f"Firecrawl crawl kickoff failed: {e}"}

    if r.status_code >= 400:
        return {"ok": False, "error": f"Firecrawl {r.status_code}: {r.text[:300]}"}

    job = r.json() or {}
    job_id = job.get("id") or job.get("jobId")
    if not job_id:
        return {"ok": False, "error": f"Firecrawl crawl returned no id: {job}"}

    # Poll up to FIRECRAWL_CRAWL_POLL_MAX seconds.
    elapsed = 0.0
    delay = 2.0
    async with httpx.AsyncClient(timeout=15.0) as c:
        while elapsed < FIRECRAWL_CRAWL_POLL_MAX:
            try:
                pr = await c.get(f"{FIRECRAWL_BASE}/crawl/{job_id}",
                                 headers={"Authorization": f"Bearer {key}"})
            except httpx.RequestError as e:
                return {"ok": False, "error": f"Firecrawl poll network error: {e}",
                        "job_id": job_id}
            if pr.status_code >= 400:
                return {"ok": False,
                        "error": f"Firecrawl poll {pr.status_code}: {pr.text[:300]}",
                        "job_id": job_id}
            body = pr.json() or {}
            status = body.get("status")
            if status == "completed":
                pages = []
                for item in (body.get("data") or [])[:limit]:
                    md = (item.get("markdown") or "")
                    pages.append({
                        "url":   (item.get("metadata") or {}).get("sourceURL")
                                 or item.get("url"),
                        "title": (item.get("metadata") or {}).get("title") or "",
                        "markdown": md[:4_000],
                        "truncated": len(md) > 4_000,
                    })
                return {"ok": True, "job_id": job_id,
                        "pages": pages, "count": len(pages)}
            if status in ("failed", "cancelled"):
                return {"ok": False, "job_id": job_id,
                        "error": f"Firecrawl crawl {status}"}
            await asyncio.sleep(delay)
            elapsed += delay
            delay = min(delay * 1.5, 6.0)

    return {"ok": False, "job_id": job_id,
            "error": f"Firecrawl crawl still running after {FIRECRAWL_CRAWL_POLL_MAX}s"}


# ── Public dispatch tables ────────────────────────────────────────────
WEB_TOOLS = {
    "web_search":                 web_search,
    "fetch_url":                  fetch_url,
    "web_search_and_summarize":   web_search_and_summarize,
    "firecrawl_scrape":           firecrawl_scrape,
    "firecrawl_crawl_site":       firecrawl_crawl_site,
}


WEB_TOOL_SPECS = [
    {
        "name": "web_search",
        "description": (
            "Live Google-style web search via Tavily. Use whenever you need "
            "FRESH facts the model doesn't know — current docs, latest release, "
            "library version, news. Returns top results with titles, URLs and "
            "snippets. Cite the URLs in your answer."
        ),
        "args_spec": {
            "query":       "string — natural-language query, <=380 chars",
            "max_results": "optional int 1–10, default 5",
            "topic":       "optional 'general'|'news', default 'general'",
            "deep":        "optional bool — set true for 'advanced' depth (2x cost)",
        },
    },
    {
        "name": "fetch_url",
        "description": (
            "Read the clean text of a public URL (or up to 5). Use when the "
            "user pastes a link, or when you have a URL from web_search and "
            "need its content. Returns markdown."
        ),
        "args_spec": {
            "url":  "string — single URL (http/https only)",
            "urls": "OR array of up to 5 URLs",
            "deep": "optional bool — 'advanced' depth (better tables/PDF)",
        },
    },
    {
        "name": "web_search_and_summarize",
        "description": (
            "Search the web AND get Tavily's own 1-paragraph answer across "
            "the result set. Use for quick fact questions where you'd "
            "otherwise need to web_search + fetch_url + read each result."
        ),
        "args_spec": {
            "query":       "string — natural-language query",
            "max_results": "optional int 1–8, default 5",
            "deep":        "optional bool — advanced depth",
        },
    },
    {
        "name": "firecrawl_scrape",
        "description": (
            "JS-rendered scrape of a single URL via Firecrawl. Use ONLY when "
            "fetch_url returns empty/garbage on a JS-heavy page (Twitter, "
            "single-page apps, dashboards). Slower + costlier than fetch_url."
        ),
        "args_spec": {
            "url":     "string — http/https URL",
            "formats": "optional ['markdown'|'html'|'screenshot'|'links']",
        },
    },
    {
        "name": "firecrawl_crawl_site",
        "description": (
            "Crawl up to N pages from a domain (default 10, max 50). Use for "
            "competitor blogs, doc sites — when one URL isn't enough."
        ),
        "args_spec": {
            "url":   "string — starting URL",
            "limit": "optional int 1–50, default 10",
        },
    },
]


async def invoke_web_tool(name: str, args: dict, ctx: dict) -> Optional[dict]:
    fn = WEB_TOOLS.get(name)
    if not fn:
        return None
    try:
        return await fn(ctx, args or {})
    except Exception as e:
        logger.exception(f"web tool {name} crashed")
        return {"ok": False, "error": f"{name} crashed: {e}"}
