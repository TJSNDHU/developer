#!/usr/bin/env python3
"""Iter 346 — one-time cleanup: bulk-close all open Vanguard/AUREM
auto-generated draft PRs (founder ruling 2026-07-29: they were never
going to be reviewed individually; dedup guard now prevents new
duplicates).

Usage:
    GITHUB_PAT=ghp_xxx REPO=polarisbuiltinc-wq/auremdev \
        python3 scripts/bulk_close_vanguard_drafts.py            # DRY RUN
    GITHUB_PAT=ghp_xxx REPO=... EXECUTE=1 \
        python3 scripts/bulk_close_vanguard_drafts.py            # real close
    ... EXECUTE=1 DELETE_BRANCHES=1 ...                          # also delete refs

Targets ONLY PRs that are (a) open, (b) draft, and (c) recognisably
auto-generated: head branch `vanguard/*` or `aurem/fix-*`, or title
starting with the Vanguard/AUREM prefixes. Human PRs are untouched.
"""
import os
import sys
import time

import requests

PAT  = os.environ.get("GITHUB_PAT")
REPO = os.environ.get("REPO")
EXECUTE = os.environ.get("EXECUTE") == "1"
DELETE_BRANCHES = os.environ.get("DELETE_BRANCHES") == "1"
API = "https://api.github.com"

if not PAT or not REPO:
    sys.exit("Set GITHUB_PAT and REPO env vars (see docstring).")

H = {"Authorization": f"Bearer {PAT}",
     "Accept": "application/vnd.github+json",
     "X-GitHub-Api-Version": "2022-11-28"}

CLOSE_COMMENT = (
    "🧹 **Bulk cleanup — superseded (dedup)**\n\n"
    "This auto-generated Vanguard security draft is being closed as part "
    "of a one-time cleanup (2026-07-29 founder decision). ~170 duplicate "
    "drafts accumulated because the scanner had no dedup guard; that "
    "guard now exists (`_create_draft_pr` fingerprint check), so future "
    "scans will update/skip instead of duplicating. The findings herein "
    "are superseded by newer scans and the current security posture of "
    "`main`. No action needed."
)


def _auto_generated(pr: dict) -> bool:
    head = ((pr.get("head") or {}).get("ref") or "")
    title = pr.get("title") or ""
    return (head.startswith("vanguard/") or head.startswith("aurem/fix-")
            or title.startswith("[AUREM]")
            or title.lower().startswith("security: vanguard"))


def main():
    targets = []
    page = 1
    while True:
        r = requests.get(f"{API}/repos/{REPO}/pulls",
                         headers=H,
                         params={"state": "open", "per_page": 100,
                                 "page": page}, timeout=30)
        r.raise_for_status()
        prs = r.json()
        for pr in prs:
            if pr.get("draft") and _auto_generated(pr):
                targets.append(pr)
        if len(prs) < 100:
            break
        page += 1

    print(f"Open draft auto-generated PRs found: {len(targets)}")
    for pr in targets:
        print(f"  #{pr['number']}  {pr['created_at']}  "
              f"{(pr.get('head') or {}).get('ref')}  {pr['title'][:60]}")

    if not EXECUTE:
        print("\nDRY RUN — set EXECUTE=1 to comment + close these PRs.")
        return

    closed = failed = 0
    for pr in targets:
        n = pr["number"]
        try:
            c = requests.post(
                f"{API}/repos/{REPO}/issues/{n}/comments",
                headers=H, json={"body": CLOSE_COMMENT}, timeout=30)
            c.raise_for_status()
            p = requests.patch(
                f"{API}/repos/{REPO}/pulls/{n}",
                headers=H, json={"state": "closed"}, timeout=30)
            p.raise_for_status()
            closed += 1
            if DELETE_BRANCHES:
                ref = (pr.get("head") or {}).get("ref")
                if ref and (ref.startswith("vanguard/")
                            or ref.startswith("aurem/fix-")):
                    requests.delete(
                        f"{API}/repos/{REPO}/git/refs/heads/{ref}",
                        headers=H, timeout=30)
            print(f"  closed #{n}")
            time.sleep(0.5)  # stay well under abuse limits
        except Exception as e:
            failed += 1
            print(f"  FAILED #{n}: {e}")

    print(f"\nDone. closed={closed} failed={failed}")


if __name__ == "__main__":
    main()
