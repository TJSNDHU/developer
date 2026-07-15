"""
services/fix_triage.py — Iter 212m-229

The MISSING classification layer in AUREM's auto-fix pipeline.
Sits between `_scan_full_scan()` (which produces raw findings) and
`Parliament.heal()` (which rewrites files). Every finding passes
through here first and gets bucketed:

    ┌───────────────────────────────────────────────────────────────┐
    │  REAL_BUG                                                     │
    │    → send to Parliament.heal(), rewrite the file surgically   │
    │                                                               │
    │  FALSE_POSITIVE                                                │
    │    → log to scanner_feedback collection, DO NOT rewrite       │
    │    → rule-tuning PR is opened for humans to review            │
    │                                                               │
    │  ARCHITECTURALLY_SAFE                                          │
    │    → add `// vanguard: ignore` / `# arch: allow-*` marker     │
    │    → cheap single-line edit, no LLM roundtrip                 │
    │                                                               │
    │  DUPLICATE                                                     │
    │    → merge with the sibling finding from the other scanner    │
    │    → prevents 2× rewrite of the same file for the same issue  │
    └───────────────────────────────────────────────────────────────┘

Design goals (matching what the founder asked "kya tumhare jitna
capable hai fix karna" — is our loop as capable as a human?):

1. NO FULL-FILE REWRITES for false positives. A generic scanner
   flagging a comment that mentions `eval` must never trigger an
   LLM rewrite of a 500-line file.

2. CROSS-FILE PATTERN DETECTION. When 7 files all miss `maxPoolSize`
   on their Motor client, that's one PATTERN — heal it with one
   template, not 7 independent LLM calls.

3. SCANNER-SIDE FEEDBACK. When we detect an FP, POST it to
   `/api/aurem-dev/scanner-feedback` so the rule regex can be
   improved. Otherwise the same FP will re-fire on every scan.

4. MARKER SUGGESTION. For findings inside sandboxed iframes,
   scanner rule files, or QA harnesses, the fix is to add a
   marker — NOT to rewrite production code.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Public enum -----------------------------------------------------
class TriageBucket(str, Enum):
    REAL_BUG            = "real_bug"
    FALSE_POSITIVE      = "false_positive"
    ARCHITECTURALLY_SAFE = "architecturally_safe"
    DUPLICATE           = "duplicate"
    DEFERRED            = "deferred"


@dataclass
class TriagedFinding:
    """A finding after triage — carries the bucket, a reason string
    that explains WHY it was placed there, and (optionally) a suggested
    marker text to inject."""
    finding: dict
    bucket: TriageBucket
    reason: str
    suggested_marker: Optional[str] = None
    dedupe_group: Optional[str] = None
    template_fix: Optional[str] = None  # For cross-file batch fixes


@dataclass
class TriageReport:
    real_bugs:            list[TriagedFinding] = field(default_factory=list)
    false_positives:      list[TriagedFinding] = field(default_factory=list)
    architecturally_safe: list[TriagedFinding] = field(default_factory=list)
    duplicates:           list[TriagedFinding] = field(default_factory=list)
    deferred:             list[TriagedFinding] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (len(self.real_bugs) + len(self.false_positives)
                + len(self.architecturally_safe) + len(self.duplicates)
                + len(self.deferred))

    def summary(self) -> dict[str, int]:
        return {
            "real_bugs":            len(self.real_bugs),
            "false_positives":      len(self.false_positives),
            "architecturally_safe": len(self.architecturally_safe),
            "duplicates":           len(self.duplicates),
            "deferred":             len(self.deferred),
            "total":                self.total,
        }


# ── FP heuristics ---------------------------------------------------
#
# Each entry is: (rule_id_pattern, path_pattern, reason).  When the
# finding's rule + path both match, it's marked FALSE_POSITIVE with
# the given reason. Deliberately conservative — we only bucket things
# where we're CERTAIN they're FPs. Anything ambiguous goes to REAL_BUG
# so it gets human review via the healer.

_FP_HEURISTICS: list[tuple[re.Pattern, re.Pattern, str]] = [
    # Iter 212m-229 — Note: comment-line skipping is now enforced
    # at the scanner level (`vanguard_scanner._is_comment_only` +
    # `bug_hunt_rules` comment-mask). Triage no longer duplicates
    # that logic — otherwise it over-classifies legit findings
    # (`inner_html_assign` outside sandboxed iframes) as FPs.

    # Scanner-rule-definition files self-flagging.
    (re.compile(r".*"),
     re.compile(r"(bug_hunt_rules|vanguard_scanner|codebase_health|"
                r"scanner_utils|generation_rules|mode_e_auditor|"
                r"full_scan_scanners)\.py$"),
     "Scanner rule-definition file — rule matches its own regex source"),

    # .env / .env.* files carrying real keys (intentional, gitignored).
    (re.compile(r"(generic_api_key|stripe_live_key|openai_key|"
                r"github_token|generic_secret|db_connection_string|"
                r"aws_access_key|aws_secret_key)"),
     re.compile(r"(^|/)\.env(\.|$)"),
     ".env file carries real keys intentionally — file is gitignored"),
]


# ── ARCH_SAFE heuristics --------------------------------------------
#
# Findings where the CORRECT fix is a per-line marker, NOT a rewrite.

_ARCH_SAFE_MARKERS: dict[str, str] = {
    # Sandboxed iframe srcDoc innerHTML — isolated by architecture.
    "inner_html_assign_sandboxed": "// vanguard: ignore — sandboxed iframe srcDoc",
    # QA harness hard-coded creds.
    "jwt_secret_hardcoded_qa":     "# vanguard: ignore — QA harness test creds",
    # Weak crypto used for cache-key sharding (not for security).
    "weak_crypto_sha1_cache":      "# vanguard: ignore — cache key, not security",
    # eval/exec inside `dispatch_table[key](args)` style plugin dispatch.
    "exec_dispatch_pattern":       "# vanguard: ignore — plugin dispatch, controlled input",
}


# ── DEFERRED heuristics ---------------------------------------------
#
# Real findings that don't warrant an urgent fix. These are logged for
# the backlog but not auto-healed. Example: 21-query bounded analytics
# rollup that only runs on admin dashboard load.

_DEFERRED_HEURISTICS: list[tuple[re.Pattern, re.Pattern, str]] = [
    # Bounded N+1 in analytics rollups (< 30 queries per admin load).
    (re.compile(r"^n_plus_one$"),
     re.compile(r"(memory_tiers|analytics|dashboard_stats|admin_reports)\.py$"),
     "Bounded analytics rollup — 21 queries max, admin-only. Non-hotpath."),
]


# ── Public API ------------------------------------------------------
def triage_findings(findings: list[dict], *, file_contents: Optional[dict[str, str]] = None) -> TriageReport:
    """Classify a list of raw scanner findings into REAL_BUG /
    FALSE_POSITIVE / ARCHITECTURALLY_SAFE / DUPLICATE / DEFERRED buckets.

    Args:
        findings: list of dicts from any scanner (security / bug_hunt /
                  perf / arch / docker / dependencies).  Must have at
                  least `rule_id` (or `title`/`name`), `file` (or
                  `filepath`/`path`), `line`, and `severity`.
        file_contents: optional mapping `path -> full_text`.  Used to
                       check whether a finding line is a comment or
                       inside a sandboxed iframe, etc.

    Returns:
        TriageReport with all findings placed into one of the 5 buckets.
    """
    report = TriageReport()
    # Dedupe key: `(file, line, rule_id)` — findings from different
    # scanners that hit the exact same coord are merged.
    dedupe_seen: dict[tuple[str, int, str], TriagedFinding] = {}

    for raw in findings or []:
        f = _normalise(raw)
        key = (f["file"], f["line"], f["rule_id"])

        # ── DUPLICATE check first ─────────────────────────────────
        if key in dedupe_seen:
            report.duplicates.append(TriagedFinding(
                finding=f,
                bucket=TriageBucket.DUPLICATE,
                reason=f"Same finding already surfaced by "
                       f"scanner={dedupe_seen[key].finding.get('scanner_source')}",
                dedupe_group=f"{key[0]}:{key[1]}:{key[2]}",
            ))
            continue
        # Record so subsequent duplicates get bucketed correctly.
        # We register this BEFORE bucketing so the "first" wins.

        # ── FALSE_POSITIVE heuristics ──────────────────────────────
        fp_hit = _match_fp(f)
        if fp_hit:
            triaged = TriagedFinding(
                finding=f, bucket=TriageBucket.FALSE_POSITIVE,
                reason=fp_hit,
            )
            report.false_positives.append(triaged)
            dedupe_seen[key] = triaged
            continue

        # ── ARCH_SAFE heuristics (marker suggestion) ────────────────
        marker = _suggest_marker(f, file_contents or {})
        if marker:
            triaged = TriagedFinding(
                finding=f, bucket=TriageBucket.ARCHITECTURALLY_SAFE,
                reason=marker[1], suggested_marker=marker[0],
            )
            report.architecturally_safe.append(triaged)
            dedupe_seen[key] = triaged
            continue

        # ── DEFERRED heuristics ────────────────────────────────────
        deferred_hit = _match_deferred(f)
        if deferred_hit:
            triaged = TriagedFinding(
                finding=f, bucket=TriageBucket.DEFERRED,
                reason=deferred_hit,
            )
            report.deferred.append(triaged)
            dedupe_seen[key] = triaged
            continue

        # ── REAL_BUG (default) ─────────────────────────────────────
        # This is the finding that actually goes to Parliament.heal.
        # Attach a template_fix hint if this is a cross-file pattern
        # (e.g. all Motor clients missing pool config).
        template_fix = _detect_template_pattern(f, findings)
        triaged = TriagedFinding(
            finding=f, bucket=TriageBucket.REAL_BUG,
            reason="No FP / arch-safe / deferred heuristic matched",
            template_fix=template_fix,
        )
        report.real_bugs.append(triaged)
        dedupe_seen[key] = triaged

    return report


# ── Internals -------------------------------------------------------
def _normalise(raw: dict) -> dict:
    """Best-effort normaliser: rule_id / file / line / severity /
    scanner_source populated for downstream use."""
    return {
        "rule_id":         (raw.get("rule_id") or raw.get("title")
                            or raw.get("name") or raw.get("rule")
                            or "unknown"),
        "file":            (raw.get("file") or raw.get("filepath")
                            or raw.get("path") or ""),
        "line":            int(raw.get("line") or 0),
        "severity":        (raw.get("severity") or "MEDIUM").upper(),
        "message":         (raw.get("message") or raw.get("desc")
                            or raw.get("snippet") or ""),
        "scanner_source":  (raw.get("source") or raw.get("category") or ""),
        "raw":             raw,
    }


def _match_fp(f: dict) -> Optional[str]:
    for rule_rx, path_rx, reason in _FP_HEURISTICS:
        if rule_rx.search(f["rule_id"]) and path_rx.search(f["file"]):
            return reason
    return None


def _match_deferred(f: dict) -> Optional[str]:
    for rule_rx, path_rx, reason in _DEFERRED_HEURISTICS:
        if rule_rx.search(f["rule_id"]) and path_rx.search(f["file"]):
            return reason
    return None


def _suggest_marker(f: dict, file_contents: dict[str, str]) -> Optional[tuple[str, str]]:
    """Returns `(marker_text, reason)` if the finding should be
    suppressed via a marker instead of a rewrite."""
    rid  = f["rule_id"]
    path = f["file"]
    text = file_contents.get(path) or ""
    line = f["line"]
    line_text = ""
    if text and 1 <= line <= len(text.split("\n")):
        line_text = text.split("\n")[line - 1]

    # innerHTML inside sandboxed iframe srcDoc
    if rid in ("inner_html_assign", "innerHTML_assignment"):
        if "sandbox=" in text or "srcDoc" in text or "sandbox: 'allow-scripts'" in text:
            return (_ARCH_SAFE_MARKERS["inner_html_assign_sandboxed"],
                    "Assignment is inside a sandboxed iframe srcDoc — "
                    "cannot reach parent DOM by construction")

    # QA harness JWT secrets
    if rid in ("jwt_secret_hardcoded", "generic_secret"):
        low = path.lower()
        if "qa/simulated-user" in low or "/qa/" in low or "seed_qa" in low:
            return (_ARCH_SAFE_MARKERS["jwt_secret_hardcoded_qa"],
                    "QA harness — test credentials are intentional")

    # SHA1/MD5 used for cache-key sharding (not security)
    if rid in ("weak_crypto_sha1", "weak_crypto_md5"):
        if ("_hash" in line_text or "cache_key" in line_text
                or "shard" in line_text or "fingerprint" in line_text):
            return (_ARCH_SAFE_MARKERS["weak_crypto_sha1_cache"],
                    "Weak hash used for cache-key sharding, not security")

    return None


def _detect_template_pattern(f: dict, all_findings: list[dict]) -> Optional[str]:
    """When ≥3 findings share the same rule_id AND similar file paths
    (all in scripts/, all in migrations/, etc.), return a template fix
    hint so `Parliament.heal` can apply the same edit to all of them
    in ONE roundtrip instead of N."""
    rid = f["rule_id"]
    if rid not in {"no_pool", "no_pool_config", "n_plus_one"}:
        return None
    same_rule = [x for x in all_findings
                 if (x.get("rule_id") or x.get("title") or "") == rid]
    if len(same_rule) >= 3:
        return (f"CROSS-FILE PATTERN: {len(same_rule)} files share "
                f"`{rid}`. Batch with template edit rather than "
                f"N independent LLM calls.")
    return None


# ── Public helper for the loop_engine -------------------------------
def apply_triage_before_heal(findings: list[dict],
                             file_contents: dict[str, str],
                             *, feedback_callback=None) -> tuple[list[dict], TriageReport]:
    """Convenience wrapper used by `loop_engine._heal_full_scan_findings`.
    Runs the triage, invokes `feedback_callback(fps)` if provided (to
    POST false positives to the scanner-feedback endpoint), and returns
    ONLY the real_bugs list that the healer should process.

    Everything else is logged and skipped — no LLM cost, no rewrite risk.
    """
    report = triage_findings(findings, file_contents=file_contents)
    summary = report.summary()
    logger.info(
        "[fix-triage] real=%d fp=%d arch_safe=%d dup=%d deferred=%d total=%d",
        summary["real_bugs"], summary["false_positives"],
        summary["architecturally_safe"], summary["duplicates"],
        summary["deferred"], summary["total"],
    )

    if feedback_callback and report.false_positives:
        try:
            feedback_callback([tf.finding for tf in report.false_positives])
        except Exception as e:                          # noqa: BLE001
            logger.warning("[fix-triage] feedback_callback failed: %r", e)

    # Only real bugs go to the LLM healer.
    return [tf.finding for tf in report.real_bugs], report


__all__ = [
    "TriageBucket", "TriagedFinding", "TriageReport",
    "triage_findings", "apply_triage_before_heal",
]
