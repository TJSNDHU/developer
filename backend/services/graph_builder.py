"""
services/graph_builder.py — Iter 165

Hybrid codebase knowledge graph:
  Step 1 (FREE)  — regex symbol + import extraction for ALL code files
  Step 2 (CHEAP) — ONE DeepSeek call describes the TOP 20 important files
                   (~4000 tokens, ~$0.001 per build)

Persists to `project_graphs` collection. Auto-refreshes via warm-start
(rebuild trigger: graph > 1 hour old). Read paths return the graph
without the heavy `nodes` field by default so the orchestrator
injection stays cheap (~300 tokens).
"""
from __future__ import annotations
import asyncio
import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

LAYER_RULES = [
    ("API",     ["/routers/", "/routes/", "/api/", "/controllers/"]),
    ("Service", ["/services/", "/service/", "/managers/", "/handlers/"]),
    ("Data",    ["/models/", "/schemas/", "/db/", "/database/"]),
    ("UI",      ["/components/", "/pages/", "/views/", "/screens/"]),
    ("Hook",    ["/hooks/", "/composables/", "/store/"]),
    ("Util",    ["/utils/", "/helpers/", "/lib/", "/shared/"]),
    ("Test",    ["/tests/", "/test/", "/__tests__/"]),
    ("Config",  ["/config/", "settings.py", "config.py", "main.py"]),
]

SKIP_DIRS = {
    # Package managers
    "node_modules", ".npm", ".yarn", ".pnpm",
    # Build outputs
    "dist", "build", ".next", ".nuxt", "out",
    ".vite", "__pycache__", ".pytest_cache",
    # Version control
    ".git",
    # Virtual envs
    "venv", ".venv", "env",
    # Coverage
    "coverage", ".coverage", "htmlcov",
    # Vendor / agent skills (AUREM specific)
    ".agent", "skills", ".understand-anything",
    # IDE
    ".idea", ".vscode",
    # Migrations (too noisy)
    "migrations", "alembic",
}

# Path-prefix filter — catches vendored bundles even when the dir name
# alone is too generic to blacklist (e.g. `skills/` exists in many repos).
SKIP_PATH_PREFIXES = (
    ".agent/",
    "skills/",
    "node_modules/",
    ".git/",
    "vendor/",
    "__pycache__/",
    "third_party/",
)

CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx",
    ".go", ".rs", ".java",
}

MAX_FILES = 200
TOP_FILES_FOR_LLM = 20


def detect_layer(path: str) -> str:
    p = (path or "").lower()
    for layer, patterns in LAYER_RULES:
        if any(pat in p for pat in patterns):
            return layer
    return "Other"


def _priority_score(path: str) -> int:
    """Lower = more important. Used to pick the top files for LLM."""
    p = (path or "").lower()
    if any(x in p for x in ["/routers/", "/services/", "orchestrator", "main.py"]):
        return 0
    if any(x in p for x in ["/components/", "/pages/", "/hooks/"]):
        return 1
    if any(x in p for x in ["/utils/", "/lib/", "/models/"]):
        return 2
    if "test" in p:
        return 10
    return 5


def extract_symbols(content: str, path: str) -> list[str]:
    """Regex only — zero LLM tokens. Returns up to 15 named symbols."""
    if not content:
        return []
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    symbols: list[str] = []

    if ext == ".py":
        symbols += re.findall(r"^(?:async\s+)?def\s+(\w+)", content, re.M)
        symbols += re.findall(r"^class\s+(\w+)", content, re.M)
    elif ext in {".js", ".jsx", ".ts", ".tsx"}:
        symbols += re.findall(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", content, re.M)
        symbols += re.findall(r"^(?:export\s+)?class\s+(\w+)", content, re.M)
        symbols += re.findall(r"^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(", content, re.M)
        symbols += re.findall(r"^export\s+default\s+function\s+(\w+)", content, re.M)
    elif ext == ".go":
        symbols += re.findall(r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)", content, re.M)
    elif ext == ".rs":
        symbols += re.findall(r"^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", content, re.M)

    seen: set = set()
    out: list[str] = []
    for s in symbols:
        if s and s not in seen and not s.startswith("_") and len(s) > 1:
            seen.add(s)
            out.append(s)
    return out[:15]


def extract_imports(content: str, path: str) -> list[str]:
    """Returns up to 8 module paths the file imports — used to build edges."""
    if not content:
        return []
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    imports: list[str] = []
    if ext == ".py":
        raw = re.findall(r"^from\s+([\w.]+)\s+import", content, re.M)
        imports = [r.replace(".", "/") for r in raw]
    elif ext in {".js", ".jsx", ".ts", ".tsx"}:
        raw = re.findall(r"""from\s+['"]([^'"]+)['"]""", content)
        imports = [r for r in raw if not r.startswith(("react", "@", "lucide"))]
    return imports[:8]


async def _llm_describe_files(files_content: dict[str, str]) -> dict[str, str]:
    """One LLM call (DeepSeek) — describes the top files in plain English.

    Returns {path: one-line description}. Returns {} on any failure so
    the regex-only graph still ships. Cost: ~4000 tokens (~$0.001)."""
    if not files_content:
        return {}
    # Route through services.llm.call_openrouter_model so we reuse the
    # single httpx client + timeout policy + OPENROUTER_API_KEY lookup.
    try:
        from .llm import call_openrouter_model
    except Exception:
        return {}

    file_list: list[str] = []
    for path, content in list(files_content.items())[:TOP_FILES_FOR_LLM]:
        name = path.rsplit("/", 1)[-1]
        preview = (content or "")[:300].replace("\n", " ").strip()
        file_list.append(f"{name}: {preview}")

    system = (
        "You are a code summariser. For each file, output ONE line in the "
        "format `<filename>: <one-line description>`. No prose, no headers, "
        "no blank lines. Each description ≤ 14 words."
    )
    user = "\n".join(file_list)

    try:
        text = await asyncio.wait_for(
            call_openrouter_model(
                model="minimax/minimax-m2.5",
                system=system, user=user,
                max_tokens=800, temperature=0.0,
            ),
            timeout=25.0,
        )
    except Exception as e:
        logger.warning("graph LLM describe failed: %r", e)
        return {}

    if not text:
        return {}

    descriptions: dict[str, str] = {}
    for line in text.strip().splitlines():
        if ":" not in line:
            continue
        name_part, desc = line.split(":", 1)
        name = name_part.strip().lstrip("•- *").rstrip(":").strip("` ")
        desc = desc.strip().strip("`").strip()
        if not name or not desc:
            continue
        # Match the bare filename back to a full path
        for path in files_content:
            if path.endswith("/" + name) or path == name or path.rsplit("/", 1)[-1] == name:
                descriptions[path] = desc[:200]
                break
    return descriptions


async def build_graph(
    db,
    project_id: str,
    user_id: str,
    gh_token: str,
    gh_owner: str,
    gh_repo: str,
    branch: str = "HEAD",
) -> dict:
    """Hybrid build with per-project gating + token-economical incremental
    mode (Iter 212m-113).

    Security: writes are scoped to {project_id, user_id} so cross-repo
    leak is impossible — a different user's project_id cannot collide
    with this user's row (compound key in Mongo upsert).

    Token economy: when a prior graph exists for this project and the
    GitHub tree SHA hasn't changed, we re-run the regex pass only and
    REUSE the existing LLM descriptions. When the tree HAS changed, we
    LLM-describe only files whose blob SHA changed since the last build
    (the typical case for small commits: 1-3 files instead of all 20).
    """
    if not (gh_token and gh_owner and gh_repo and project_id and user_id):
        logger.warning(
            "build_graph: missing required args "
            "(project_id=%s user_id=%s owner=%s repo=%s token=%s)",
            bool(project_id), bool(user_id), bool(gh_owner),
            bool(gh_repo), bool(gh_token),
        )
        return {}

    import httpx
    headers = {
        "Authorization": f"token {gh_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "aurem-graph-builder",
    }
    base = f"https://api.github.com/repos/{gh_owner}/{gh_repo}"

    # ── Load previous graph (for incremental + LLM description reuse) ──
    prior: dict = {}
    if db is not None:
        try:
            prior = await db.project_graphs.find_one(
                {"project_id": project_id, "user_id": user_id},
                {"_id": 0},
            ) or {}
        except Exception as e:
            logger.debug("prior graph load failed: %r", e)
            prior = {}
    prior_tree_sha: str = prior.get("tree_sha") or ""
    prior_blob_shas: dict[str, str] = prior.get("blob_shas") or {}
    prior_descriptions: dict[str, str] = {
        p: (n or {}).get("description") or ""
        for p, n in (prior.get("nodes") or {}).items()
        if (n or {}).get("description")
    }

    # Step 1A — file tree (1 API call) — captures both tree SHA and
    # per-blob SHA so we can do fingerprint-based incremental updates.
    try:
        async with httpx.AsyncClient(timeout=15.0) as cx:
            r = await cx.get(f"{base}/git/trees/{branch}?recursive=1", headers=headers)
            r.raise_for_status()
            payload = r.json() or {}
            tree = payload.get("tree") or []
            tree_sha = payload.get("sha") or ""
    except Exception as e:
        logger.error("graph tree fetch failed: %r", e)
        return {}

    # Step 1B — filter + priority sort + blob-SHA map
    all_files: list[str] = []
    blob_shas: dict[str, str] = {}
    for item in tree:
        if (item or {}).get("type") != "blob":
            continue
        path = (item or {}).get("path", "")
        if not path:
            continue
        ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
        parts = path.split("/")
        if any(part in SKIP_DIRS for part in parts):
            continue
        if any(path.startswith(pref) for pref in SKIP_PATH_PREFIXES):
            continue
        if ext not in CODE_EXTENSIONS:
            continue
        all_files.append(path)
        blob_shas[path] = (item or {}).get("sha") or ""
    all_files = sorted(all_files, key=_priority_score)[:MAX_FILES]
    top_files = all_files[:TOP_FILES_FOR_LLM]

    # Iter 212m-113 — Identify which top files CHANGED since the last
    # build. Only these need a fresh LLM description; the rest reuse
    # the cached description. Drops typical 1-file-changed token cost
    # from ~4000 → ~200 tokens.
    changed_top: list[str] = [
        p for p in top_files
        if blob_shas.get(p) != prior_blob_shas.get(p)
    ]
    reused_top: list[str] = [
        p for p in top_files if p not in changed_top
        and p in prior_descriptions
    ]
    logger.info(
        "graph incremental: %d/%d top files changed (will re-LLM); "
        "%d reuse prior descriptions",
        len(changed_top), len(top_files), len(reused_top),
    )

    # Step 1C — parallel file reads (batched by 10)
    async def _read(path: str) -> tuple[str, str]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as cx:
                r = await cx.get(
                    f"{base}/contents/{path}",
                    params={"ref": branch} if branch != "HEAD" else None,
                    headers={**headers, "Accept": "application/vnd.github.raw+json"},
                )
                if r.status_code == 200:
                    return path, (r.text or "")[:6000]
        except Exception:
            pass
        return path, ""

    all_contents: dict[str, str] = {}
    for i in range(0, len(all_files), 10):
        batch = all_files[i:i + 10]
        results = await asyncio.gather(
            *[_read(p) for p in batch], return_exceptions=True,
        )
        for res in results:
            if isinstance(res, tuple):
                p, c = res
                all_contents[p] = c
        await asyncio.sleep(0.05)

    # Step 1D — regex pass over all files
    nodes: dict[str, dict] = {}
    for path, content in all_contents.items():
        nodes[path] = {
            "path":        path,
            "layer":       detect_layer(path),
            "symbols":     extract_symbols(content, path),
            "imports":     extract_imports(content, path),
            "description": prior_descriptions.get(path, ""),
            "size":        len(content or ""),
            "sha":         blob_shas.get(path, ""),
        }

    # Step 2 — single LLM call ONLY for CHANGED top files. Skips the
    # call entirely if every top file already has a cached description.
    descriptions: dict[str, str] = {}
    if changed_top:
        changed_contents = {p: all_contents[p] for p in changed_top if p in all_contents}
        if changed_contents:
            descriptions = await _llm_describe_files(changed_contents)
    for path, desc in descriptions.items():
        if path in nodes:
            nodes[path]["description"] = desc

    # Layer map
    layers: dict[str, list[str]] = {}
    for path, node in nodes.items():
        layers.setdefault(node["layer"], []).append(path)

    # Edges (file → file dependencies via matched basenames)
    edges: list[dict] = []
    base_to_path: dict[str, str] = {}
    for cand in nodes:
        base_to_path[cand.rsplit("/", 1)[-1].rsplit(".", 1)[0]] = cand
    for path, node in nodes.items():
        for imp in node.get("imports", []):
            imp_base = imp.rsplit("/", 1)[-1]
            target = base_to_path.get(imp_base)
            if target and target != path:
                edges.append({"from": path, "to": target})
    edges = edges[:300]

    described_count = sum(
        1 for n in nodes.values() if n.get("description")
    )
    graph = {
        "project_id":   project_id,
        "user_id":      user_id,
        "built_at":     time.time(),
        "file_count":   len(nodes),
        "nodes":        nodes,
        "layers":       layers,
        "edges":        edges,
        "status":       "ready",
        # Iter 212m-113 — `llm_files` is now the TOTAL count of
        # described files (cached + newly described) — not just the
        # LLM calls this build, which can be 0 on incremental no-ops.
        "llm_files":    described_count,
        "tree_sha":     tree_sha,
        "blob_shas":    blob_shas,
        "tree_sha_prev": prior_tree_sha,
        "llm_changed":  len(changed_top),
        "llm_reused":   len(reused_top),
    }

    if db is not None:
        try:
            # Compound-key upsert guarantees per-project isolation. A
            # different user_id with the same project_id (impossible
            # in our schema but defensive) would write a separate doc.
            await db.project_graphs.update_one(
                {"project_id": project_id, "user_id": user_id},
                {"$set": graph},
                upsert=True,
            )
        except Exception as e:
            logger.warning("graph save failed: %r", e)

    logger.info(
        "graph built: project=%s user=%s | %d files | %d described "
        "(%d new + %d cached) | %d edges | tree_sha=%s",
        project_id, user_id,
        len(nodes), described_count, len(changed_top), len(reused_top),
        len(edges), tree_sha[:8] if tree_sha else "?",
    )
    return graph


async def get_graph(db, project_id: str, user_id: str) -> dict:
    """Light read (excludes heavy `nodes` field). Used by the FE list view."""
    if db is None or not project_id or not user_id:
        return {}
    try:
        doc = await db.project_graphs.find_one(
            {"project_id": project_id, "user_id": user_id},
            {"_id": 0, "nodes": 0},
        )
    except Exception:
        return {}
    return doc or {}


async def get_graph_full(db, project_id: str, user_id: str) -> dict:
    """Full read including `nodes`. Used by the FE expanded file view."""
    if db is None or not project_id or not user_id:
        return {}
    try:
        doc = await db.project_graphs.find_one(
            {"project_id": project_id, "user_id": user_id},
            {"_id": 0},
        )
    except Exception:
        return {}
    return doc or {}


async def get_graph_for_agent(db, project_id: str, user_id: str) -> str:
    """Compact (~300 token) layer summary for orchestrator injection.
    Returns "" if no graph exists yet — never raises."""
    graph = await get_graph(db, project_id, user_id)
    if not graph:
        return ""
    layers = graph.get("layers") or {}
    lines = ["[CODEBASE GRAPH]"]
    priority = ["API", "Service", "UI", "Data", "Hook", "Util", "Config"]
    for layer in priority:
        files = layers.get(layer) or []
        if not files:
            continue
        names = [f.rsplit("/", 1)[-1] for f in files[:4]]
        suffix = f" +{len(files) - 4} more" if len(files) > 4 else ""
        lines.append(f"{layer}: {', '.join(names)}{suffix}")
    lines.append(f"Total: {graph.get('file_count', 0)} files")
    return "\n".join(lines)
