"""
HTTP proxy to upstream AUREM's tool registry.
ORA CTO calls https://aurem.live/api/ora-tools/{list,execute} with shared JWT.

The upstream tools catalog is OPTIONAL. When the deployment is not paired
with an aurem.live account, both endpoints return 401 — which is expected,
not an error. We log those at INFO level so production logs stay clean.
Set DISABLE_UPSTREAM_TOOLS=1 to skip the HTTP calls entirely.
"""
import os
import re
import json
import httpx
import logging

logger = logging.getLogger(__name__)

UPSTREAM_URL = os.getenv("AUREM_UPSTREAM_URL", "https://aurem.live")
_UPSTREAM_DISABLED = os.getenv("DISABLE_UPSTREAM_TOOLS", "").lower() in (
    "1", "true", "yes"
)
# Once the upstream returns 401/403 the first time, stop retrying for the
# lifetime of this process — saves bandwidth and silences log spam.
_upstream_giving_up = False

# Same regex as upstream gateway for tool call extraction
_TOOL_CALL_RE = re.compile(
    r'```(?:tool_call|json)\s*\n(.*?)\n```',
    re.DOTALL | re.IGNORECASE
)


async def list_tools(jwt_token: str) -> list[dict]:
    """GET upstream /api/ora-tools/list → returns tool catalog.
    Returns [] silently if upstream is disabled / unauthorized / unreachable."""
    global _upstream_giving_up
    if _UPSTREAM_DISABLED or _upstream_giving_up:
        return []
    url = f"{UPSTREAM_URL}/api/ora-tools/list"
    headers = {"Authorization": f"Bearer {jwt_token}"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data.get("tools", [])
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status in (401, 403, 404):
            # Expected when this deployment isn't tied to an aurem.live account
            _upstream_giving_up = True
            logger.info(
                f"upstream tools disabled (HTTP {status} from {UPSTREAM_URL}). "
                "Continuing with built-in capabilities only."
            )
        else:
            logger.warning(f"list_tools upstream HTTP {status}")
        return []
    except Exception as e:
        logger.warning(f"list_tools unreachable: {type(e).__name__}")
        return []


async def invoke_tool(name: str, args: dict, jwt_token: str) -> dict:
    """POST upstream /api/ora-tools/execute → returns tool result dict.
    Short-circuits when upstream is known-unavailable."""
    global _upstream_giving_up
    if _UPSTREAM_DISABLED or _upstream_giving_up:
        return {"ok": False, "error": "upstream tools unavailable", "tool": name}
    url = f"{UPSTREAM_URL}/api/ora-tools/execute"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json"
    }
    payload = {"tool": name, "args": args}

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status in (401, 403):
            _upstream_giving_up = True
            logger.info(f"upstream tools disabled (HTTP {status})")
        else:
            logger.warning(f"invoke_tool {name} HTTP {status}")
        return {"ok": False, "error": f"HTTP {status}", "tool": name}
    except Exception as e:
        logger.warning(f"invoke_tool {name} unreachable: {type(e).__name__}")
        return {"ok": False, "error": str(e), "tool": name}


def extract_tool_calls(text: str) -> list[dict]:
    """
    Parse tool calls from LLM output. Supports 3 emission shapes:
      1. ```tool_call / ```json fenced JSON (primary — Groq llama-3.3)
      2. Bare {"tool": "...", "args": {...}} with no fence (qwen/Haiku)
      3. Bare {"name": "...", "parameters": {...}} (OpenAI-style)

    iter 323ad — added shapes 2 & 3 to stop the "raw JSON dikh raha hai"
    bug where the parser missed unfenced emissions.
    """
    calls: list[dict] = []
    seen_blocks: set[str] = set()

    # Shape 1 — fenced
    for match in _TOOL_CALL_RE.finditer(text):
        block = match.group(1).strip()
        seen_blocks.add(block)
        try:
            data = json.loads(block)
            if isinstance(data, dict) and "tool" in data:
                calls.append({
                    "tool": data["tool"],
                    "args": data.get("args", {})
                })
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in tool_call block: {block[:100]}")

    if calls:
        return calls

    # Shape 2 & 3 fallback — bare JSON object containing "tool" or "name".
    # Conservative non-greedy single-level brace match; nested JSON args
    # are accepted up to one nesting level.
    bare_pattern = re.compile(
        r'\{(?:[^{}]|\{[^{}]*\})*\}',
        re.DOTALL
    )
    for raw in bare_pattern.findall(text):
        if raw in seen_blocks:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        tool_name = data.get("tool") or data.get("name") or data.get("function")
        if not isinstance(tool_name, str):
            continue
        tool_args = (
            data.get("args")
            or data.get("parameters")
            or data.get("arguments")
            or {}
        )
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except json.JSONDecodeError:
                tool_args = {}
        calls.append({"tool": tool_name, "args": tool_args})

    return calls


def strip_tool_calls(text: str) -> str:
    """Remove any ```tool_call``` / ```json``` tool-call fences from text.

    Iter 35: the orchestrator EXECUTES tool calls behind the scenes; the
    user must never see them in the final streamed answer. Previously,
    when the LLM emitted a tool_call fence in its FINAL iteration (max
    iters hit without convergence), the raw JSON got streamed to the UI
    and rendered as a markdown code block — exact bug the user reported.
    """
    if not text:
        return text
    cleaned = _TOOL_CALL_RE.sub("", text)
    # Collapse runs of >2 blank lines that the fence removal might have left
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()



# Iter 36: detect hallucinated citations (line numbers, fabricated metrics)
# in an AI reply that lack supporting tool evidence. Returns a list of
# offending excerpts. Used by the orchestrator to either strip them or
# tag the reply with a "may be fabricated" warning. The check is
# conservative: only flag a citation if the cited file path was NOT
# among the tool results this turn.
_LINE_CITATION_RE = re.compile(
    r"(?:line|lines)\s+(\d{1,5})(?:\s*[-–]\s*\d{1,5})?",
    re.IGNORECASE,
)
_FAKE_METRIC_RE = re.compile(
    r"\b\d{1,3}\s*%\s+(?:of|reduction|improvement|less|fewer|faster|"
    r"more|stress|test|failure|success)",
    re.IGNORECASE,
)
_FILE_PATH_RE = re.compile(
    r"`([a-zA-Z0-9_./-]+\.(?:py|jsx?|tsx?|md|json|ya?ml|toml|html|css))`"
)


def detect_unsourced_citations(reply: str, tool_paths_read: set[str]) -> list[str]:
    """Scan an AI reply for line-number references and fabricated-looking
    metrics. Returns a deduped list of suspicious snippets. Empty if
    every citation is backed by a file actually fetched this turn."""
    if not reply:
        return []
    flags: list[str] = []
    # Any file path referenced in backticks that wasn't fetched this turn
    for m in _FILE_PATH_RE.finditer(reply):
        path = m.group(1)
        if path not in tool_paths_read and "/" in path:
            flags.append(f"path `{path}` not read this turn")
    # Line-number references (line N, lines N-M) regardless of file
    for m in _LINE_CITATION_RE.finditer(reply):
        if not tool_paths_read:
            flags.append(f"line citation '{m.group(0)}' with no file fetched")
            break  # one flag is enough
    # Suspicious metric language ("reduces failures by 83%")
    for m in _FAKE_METRIC_RE.finditer(reply):
        flags.append(f"fabricated-style metric: '{m.group(0)}'")
    # Dedup while preserving order
    seen, out = set(), []
    for f in flags:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out[:6]  # cap so the warning footer stays short
