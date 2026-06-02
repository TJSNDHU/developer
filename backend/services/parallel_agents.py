"""
services/parallel_agents.py
Splits one big code task across 3 concurrent DeepSeek workers:

  • Backend agent  — FastAPI routes / services / models
  • Frontend agent — React components / hooks / CSS
  • Config agent   — tests, env, README

All three DeepSeek calls fire simultaneously via `asyncio.gather()`.
Claude reviews the merged output once (not three times — saves tokens).
Trivial tasks (< 3 files, single domain) skip parallelisation entirely.
Each agent gets only its slice of the file tree and a short, role-
specific system prompt (~200 tokens).
"""

from __future__ import annotations
import asyncio
import re
from typing import Optional
from services.llm import call_llm_with_meta   # existing in your codebase


# ─────────────────────────────────────────────────────────────────────────────
# Task decomposer — decides if parallel is needed
# ─────────────────────────────────────────────────────────────────────────────

def should_parallelize(task_description: str, file_tree: list[str]) -> bool:
    """
    Returns True if task spans multiple domains (backend + frontend + tests).
    Small single-file tasks run single-agent as before — saves tokens.
    """
    desc = task_description.lower()

    has_frontend = any(k in desc for k in ["react", "component", "ui", "page", "css", "frontend", "jsx", "tsx"])
    has_backend  = any(k in desc for k in ["api", "route", "endpoint", "fastapi", "service", "model", "schema"])
    has_tests    = any(k in desc for k in ["test", "pytest", "spec", "coverage"])
    has_full     = any(k in desc for k in ["full", "complete", "entire", "whole", "both", "all"])

    return (has_frontend and has_backend) or has_full or (len(file_tree) > 8)


def decompose_task(task_description: str, repo_ctx: str, file_tree: list[str]) -> list[dict]:
    """
    Splits task description into domain-specific sub-tasks.
    Returns list of agent configs: [{role, focus, system_prompt, relevant_files}]
    """
    # Filter file tree by domain
    backend_files  = [f for f in file_tree if any(p in f for p in ["routers/", "services/", "models/", ".py"])]
    frontend_files = [f for f in file_tree if any(p in f for p in ["components/", "pages/", ".jsx", ".tsx", ".css"])]
    config_files   = [f for f in file_tree if any(p in f for p in ["test", "config", ".env", ".md", "requirements"])]

    agents = []

    if backend_files:
        agents.append({
            "role": "backend",
            "focus": "Backend only: FastAPI routes, services, MongoDB models.",
            "system": f"""You are a backend engineer. Task: {task_description}
Repo: {repo_ctx}
ONLY modify backend files. Output FILE blocks only. No explanations.
Relevant files: {', '.join(backend_files[:10])}""",
            "files": backend_files[:10],
        })

    if frontend_files:
        agents.append({
            "role": "frontend",
            "focus": "Frontend only: React components, hooks, CSS.",
            "system": f"""You are a frontend engineer. Task: {task_description}
Repo: {repo_ctx}
ONLY modify frontend files. Output FILE blocks only. No explanations.
No inline styles. No `transition: all`. Use existing CSS classes.
Relevant files: {', '.join(frontend_files[:10])}""",
            "files": frontend_files[:10],
        })

    if config_files or (not agents):
        agents.append({
            "role": "config",
            "focus": "Tests, config, docs only.",
            "system": f"""You are a QA/config engineer. Task: {task_description}
Repo: {repo_ctx}
Write/update tests and config files only. Output FILE blocks only. No explanations.
Relevant files: {', '.join(config_files[:8])}""",
            "files": config_files[:8],
        })

    # Fallback: if decomposition gave nothing, single agent
    if not agents:
        agents.append({
            "role": "general",
            "focus": "Full task.",
            "system": f"Task: {task_description}\nRepo: {repo_ctx}\nOutput FILE blocks only.",
            "files": file_tree[:15],
        })

    return agents


# ─────────────────────────────────────────────────────────────────────────────
# Parallel runner
# ─────────────────────────────────────────────────────────────────────────────

async def _run_single_agent(agent: dict, user_message: str) -> dict:
    """Runs one DeepSeek agent. Returns {role, output, error}."""
    try:
        resp = await call_llm_with_meta(
            system=agent["system"],
            user=user_message,
            mode="code",
            max_tokens=3000,
        )
        # call_llm_with_meta returns a dict (ok/content/provider/...)
        output_text = (resp or {}).get("content", "") if isinstance(resp, dict) else str(resp or "")
        if isinstance(resp, dict) and resp.get("ok") is False:
            return {"role": agent["role"], "output": "", "error": resp.get("error", "llm not ok")}
        return {"role": agent["role"], "output": output_text, "error": None}
    except Exception as e:
        return {"role": agent["role"], "output": "", "error": str(e)}


async def run_parallel_agents(
    task_description: str,
    repo_ctx: str,
    file_tree: list[str],
) -> dict:
    """
    Main entry point. Runs 2-3 agents in parallel, merges output.

    Returns:
        {
            "file_blocks": dict,         # merged {filepath: content}
            "agent_results": list,       # per-agent raw outputs
            "parallelized": bool,        # True if parallel ran
            "agents_used": int,
        }
    """
    # Decide: parallel or single?
    if not should_parallelize(task_description, file_tree):
        # Single agent — same as before
        resp = await call_llm_with_meta(
            system=f"Task: {task_description}\nRepo: {repo_ctx}\nOutput FILE blocks only.",
            user=task_description,
            mode="code",
            max_tokens=4000,
        )
        output = (resp or {}).get("content", "") if isinstance(resp, dict) else str(resp or "")
        return {
            "file_blocks": parse_file_blocks(output),
            "agent_results": [{"role": "general", "output": output}],
            "parallelized": False,
            "agents_used": 1,
        }

    # Decompose into domain agents
    agents = decompose_task(task_description, repo_ctx, file_tree)

    # Fire all agents simultaneously
    tasks = [_run_single_agent(agent, task_description) for agent in agents]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    # Merge all FILE blocks — later agents overwrite earlier on conflict
    merged_blocks: dict = {}
    for result in results:
        if result["error"]:
            continue
        blocks = parse_file_blocks(result["output"])
        merged_blocks.update(blocks)

    return {
        "file_blocks": merged_blocks,
        "agent_results": results,
        "parallelized": True,
        "agents_used": len(agents),
    }


# ─────────────────────────────────────────────────────────────────────────────
# File block parser (shared utility)
# ─────────────────────────────────────────────────────────────────────────────

def parse_file_blocks(raw: str) -> dict:
    """
    Parses FILE: path\\n```\\ncontent\\n``` blocks from LLM output.
    Returns {filepath: content} dict.
    """
    result = {}
    lines = raw.split("\n")
    current_file = None
    in_block = False
    block_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("FILE:"):
            if current_file and block_lines:
                result[current_file] = "\n".join(block_lines).strip()
                block_lines = []
                in_block = False
            current_file = stripped.replace("FILE:", "").strip()

        elif stripped.startswith("```") and current_file:
            if not in_block:
                in_block = True
            else:
                result[current_file] = "\n".join(block_lines).strip()
                block_lines = []
                in_block = False

        elif in_block:
            block_lines.append(line)

    # Handle unclosed block
    if current_file and block_lines and in_block:
        result[current_file] = "\n".join(block_lines).strip()

    return result
