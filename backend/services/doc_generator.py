"""
doc_generator.py — AUREM Dev
Generates 6 planning docs before any project build.
All calls: temperature=0.0, JSON output only.
"""
from __future__ import annotations
import json
import logging

from services.llm import call_llm

logger = logging.getLogger(__name__)

_SYS = "Output valid JSON only. No markdown fences. No explanation."

DOCS = {
    "prd": {
        "name": "Product Requirements Document",
        "max_tokens": 800,
        "prompt": (
            'Senior PM. Output PRD as JSON: {"app_name":"","one_liner":"",'
            '"target_users":[],"problem":"","core_features":[],"user_roles":[],'
            '"user_stories":[],"mvp_scope":[],"out_of_scope":[],"success_metrics":[]}. '
            'App: {idea}'
        ),
    },
    "trd": {
        "name": "Technical Requirements Document",
        "max_tokens": 500,
        "prompt": (
            'Senior architect. Output TRD as JSON: {"frontend":"","backend":"",'
            '"database":"","auth":"","apis":[],"deployment":"","security":[],'
            '"integrations":[]}. App: {idea}'
        ),
    },
    "app_flow": {
        "name": "App Flow Document",
        "max_tokens": 500,
        "prompt": (
            'UX strategist. Output app flow as JSON: {"pages":[],"user_journey":[],'
            '"navigation":[],"auth_flow":[],"error_states":[]}. App: {idea}'
        ),
    },
    "ui_ux": {
        "name": "UI/UX Design Brief",
        "max_tokens": 400,
        "prompt": (
            'Senior UI designer. Output design brief as JSON: {"style":"",'
            '"colors":{"primary":"","background":"","text":"","accent":""},'
            '"typography":{"heading":"","body":"","code":""},"layout":"",'
            '"ux_principles":[]}. App: {idea}'
        ),
    },
    "schema": {
        "name": "Backend Schema",
        "max_tokens": 600,
        "prompt": (
            'Senior backend engineer. Output DB schema as JSON: {"tables":'
            '[{"name":"","columns":[{"name":"","type":"","constraints":""}],'
            '"indexes":[],"relationships":[]}]}. App: {idea}'
        ),
    },
    "plan": {
        "name": "Implementation Plan",
        "max_tokens": 500,
        "prompt": (
            'Senior engineer. Output implementation plan as JSON: '
            '{"phases":[{"phase":1,"name":"Setup","steps":[],"deliverables":[]}]}. '
            'App: {idea}'
        ),
    },
}


def _strip_fences(s: str) -> str:
    t = s.strip()
    for fence in ("```json", "```JSON", "```"):
        t = t.replace(fence, "")
    return t.strip()


async def generate_all_docs(idea: str) -> dict:
    """Generate all 6 docs. Returns {doc_key: {name, ok, data, [error]}}."""
    results: dict = {}
    for key, cfg in DOCS.items():
        prompt = cfg["prompt"].replace("{idea}", (idea or "")[:300])
        try:
            raw = await call_llm(
                messages=[{"role": "user", "content": prompt}],
                system=_SYS,
                max_tokens=cfg["max_tokens"],
                temperature=0.0,
                mode="code",
            )
            data = json.loads(_strip_fences(raw))
            results[key] = {"name": cfg["name"], "ok": True, "data": data}
        except Exception as e:
            logger.error(f"[doc_gen] {key} failed: {e}")
            results[key] = {
                "name": cfg["name"], "ok": False,
                "error": str(e), "data": {},
            }
    return results
