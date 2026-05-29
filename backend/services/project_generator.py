"""
project_generator.py — AUREM Dev
AI generates a complete full-stack project from a plain-English idea.

Flow:
  1. receive_idea(idea, stack_id) — user describes what they want
  2. generate_plan() — LLM returns numbered build plan
  3. generate_code() — LLM returns file tree + file contents
  4. write_to_disk() — saves files to workspace/{project_id}/
  5. returns {project_id, files, plan}
"""
from __future__ import annotations
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

WORKSPACE = Path(os.getenv("WORKSPACE_PATH", "/tmp/aurem-dev-projects"))

SYSTEM_PROMPT = """You are AUREM Dev, an expert full-stack developer.
When given an idea, you output a complete working project.

OUTPUT FORMAT — respond with valid JSON only:
{
  "plan": ["step 1 description", "step 2 description", ...],
  "files": [
    {"path": "frontend/src/App.jsx", "content": "...full file content..."},
    {"path": "backend/main.py", "content": "...full file content..."},
    {"path": "docker-compose.yml", "content": "...full file content..."}
  ],
  "stack": "react-fastapi",
  "description": "one line description of what was built"
}

Rules:
- Always include: frontend, backend, docker-compose.yml, README.md
- Use the stack the user picked or default to react-fastapi
- Write COMPLETE file contents — no placeholders, no "..." 
- Backend: FastAPI + MongoDB (motor)
- Frontend: React + Tailwind
- All env vars go in .env.example
"""


async def generate_project(
    idea: str,
    stack_id: str = "react-fastapi",
    max_tokens: int = 8000,
) -> dict:
    """Generate a full project from an idea. Returns project_id + files."""
    from services.llm import call_llm

    project_id = uuid.uuid4().hex[:10]
    logger.info(f"[generator] project_id={project_id} stack={stack_id} idea={idea[:60]}")

    prompt = (
        f"Build this project: {idea}\n\n"
        f"Use the {stack_id} stack.\n"
        "Return the full project as JSON following the format above."
    )

    try:
        raw = await call_llm(
            messages=[{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
            max_tokens=max_tokens,
            temperature=0.0,
            mode="code",
        )
        data = json.loads(raw)
    except Exception as e:
        logger.error(f"[generator] LLM failed: {e}")
        return {"ok": False, "error": str(e), "project_id": project_id}

    # Write files to disk
    project_dir = WORKSPACE / project_id
    written = []
    for f in data.get("files", []):
        fpath = project_dir / f["path"]
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(f["content"], encoding="utf-8")
        written.append(f["path"])

    logger.info(f"[generator] wrote {len(written)} files to {project_dir}")
    return {
        "ok": True,
        "project_id": project_id,
        "stack": data.get("stack", stack_id),
        "description": data.get("description", ""),
        "plan": data.get("plan", []),
        "files": written,
        "workspace_path": str(project_dir),
    }


async def get_project_files(project_id: str) -> list[dict]:
    """Read all files from a generated project."""
    project_dir = WORKSPACE / project_id
    if not project_dir.exists():
        return []
    result = []
    for fpath in sorted(project_dir.rglob("*")):
        if fpath.is_file():
            try:
                result.append({
                    "path": str(fpath.relative_to(project_dir)),
                    "content": fpath.read_text(encoding="utf-8"),
                    "size": fpath.stat().st_size,
                })
            except Exception:
                pass
    return result
