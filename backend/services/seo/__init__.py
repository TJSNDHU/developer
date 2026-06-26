"""
services/seo/ — Aurem SEO autofix engine (PR-1, Iter 212m-29).

Plan tier matrix:
  Swift  — Category A : meta tags, schema markup, robots.txt, sitemap, image alts
  Pro    — A + B      : Swift + headings (deferred to PR-2), lazy loading (PR-2)
  Maxx   — A + B + C  : Pro + GSC indexing (PR-3, deferred)

Each fixer in this package:
  - Reads files from GitHub via the existing repo_context fetcher
    (NO local filesystem, NO local repo clone — matches the rest of
    the codebase's "REST-only" model)
  - Returns a list of `{"path": str, "before": str|None, "after": str}`
    patches (None `before` = create new file)
  - Is pure / async-safe / unit-testable WITHOUT network
"""
from .orchestrator import run_seo_fixes, PLAN_FEATURES, SeoOptions

__all__ = ["run_seo_fixes", "PLAN_FEATURES", "SeoOptions"]
