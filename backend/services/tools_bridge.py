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
import time
import httpx
import logging

logger = logging.getLogger(__name__)

UPSTREAM_URL = os.getenv("AUREM_UPSTREAM_URL", "https://aurem.live")
_UPSTREAM_DISABLED = os.getenv("DISABLE_UPSTREAM_TOOLS", "").lower() in (
    "1", "true", "yes"
)
# Iter 138 — once the upstream returns 401/403 we back off, but ONLY
# for a cooldown window (default 5 min). Previously we set
# `_upstream_giving_up=True` permanently for the lifetime of the
# process — which meant a transient upstream outage at boot
# permanently disabled the entire upstream tools catalog until the
# pod restarted. Now we still skip the call during the cooldown, but
# automatically reopen the circuit when the window expires.
_upstream_giving_up: bool = False
_upstream_giving_up_until: float = 0.0  # monotonic timestamp
_UPSTREAM_COOLDOWN_S = float(os.getenv("UPSTREAM_TOOLS_COOLDOWN_S", "300"))


def _upstream_blocked() -> bool:
    """Return True if upstream calls should be skipped right now."""
    global _upstream_giving_up, _upstream_giving_up_until
    if _UPSTREAM_DISABLED:
        return True
    if _upstream_giving_up:
        if time.monotonic() >= _upstream_giving_up_until:
            # Cooldown expired — reopen the circuit and try again.
            _upstream_giving_up = False
            return False
        return True
    return False


def _open_upstream_cooldown() -> None:
    """Block upstream calls for the cooldown window."""
    global _upstream_giving_up, _upstream_giving_up_until
    _upstream_giving_up = True
    _upstream_giving_up_until = time.monotonic() + _UPSTREAM_COOLDOWN_S


# Iter 212m-41 — extended tool-call stripper.
#
# The old regex only matched fenced JSON (```tool_call / ```json …```),
# which Claude + GLM started bypassing in production by emitting:
#   (1)  bare JSON objects on their own line, e.g.
#        `{"name": "fetch_url", "arguments": {"url": "..."}}`
#   (2)  OpenAI-style envelope:
#        `{"tool_calls":[{"name":"X","arguments":{...}}]}`
#   (3)  XML-style fences:  `<tool_call>...</tool_call>`
#   (4)  Verbose pre/postamble like `Calling fetch_url with:` followed
#        by a JSON blob.
# All four leak into Ask Advisor turns where there's no orchestrator
# tool loop to consume them, surfacing in the UI as raw JSON.  This
# patch widens the stripper without changing the call sites.
_TOOL_CALL_RE = re.compile(
    r'```(?:tool_call|tool|json|function|function_call)\s*\n(.*?)\n```',
    re.DOTALL | re.IGNORECASE
)

_TOOL_CALL_XML_RE = re.compile(
    r'<\s*(?:tool_call|function_call|tool|function)\b[^>]*>'
    r'(.*?)</\s*(?:tool_call|function_call|tool|function)\s*>',
    re.DOTALL | re.IGNORECASE,
)

# Bare JSON object whose top-level key looks like a tool invocation.
# We anchor on the keys we care about so we don't strip legitimate
# JSON the user might be discussing (e.g. an API response sample).
_TOOL_CALL_BARE_JSON_RE = re.compile(
    r'(?:^|\n)\s*\{\s*"(?:tool_calls?|function_call|name)"\s*:\s*'
    r'(?:"[A-Za-z_][\w.-]*"|\[).*?\}\s*(?=\n|$)',
    re.DOTALL,
)

# OpenAI-style preamble noise ("Calling X with: { ... }")
_TOOL_CALL_PREAMBLE_RE = re.compile(
    r'(?:^|\n)\s*(?:Calling|Invoking|Executing|Running|I will (?:call|use))\s+'
    r'`?[a-zA-Z_][\w.-]*`?\s+(?:with|:)\s*\n?\s*\{.*?\}\s*(?=\n|$)',
    re.DOTALL | re.IGNORECASE,
)


async def list_tools(jwt_token: str) -> list[dict]:
    """GET upstream /api/ora-tools/list → returns tool catalog.
    Returns [] silently if upstream is disabled / unauthorized / unreachable."""
    if _upstream_blocked():
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
            _open_upstream_cooldown()
            logger.info(
                f"upstream tools disabled (HTTP {status} from {UPSTREAM_URL}). "
                f"Continuing with built-in capabilities only. "
                f"Will retry in {int(_UPSTREAM_COOLDOWN_S)}s."
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
    if _upstream_blocked():
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
            _open_upstream_cooldown()
            logger.info(
                f"upstream tools temporarily disabled (HTTP {status}); "
                f"will retry in {int(_UPSTREAM_COOLDOWN_S)}s"
            )
        else:
            logger.warning(f"invoke_tool {name} HTTP {status}")
        return {"ok": False, "error": f"HTTP {status}", "tool": name}
    except Exception as e:
        logger.warning(f"invoke_tool {name} unreachable: {type(e).__name__}")
        return {"ok": False, "error": str(e), "tool": name}


def extract_tool_calls(text: str) -> list[dict]:
    """
    Parse tool calls from LLM output. Supports 4 emission shapes:
      1. ```tool_call / ```json fenced JSON (primary — Groq llama-3.3)
      2. Bare {"tool": "...", "args": {...}} with no fence (qwen/Haiku)
      3. Bare {"name": "...", "parameters": {...}} (OpenAI-style)
      4. Python function-call syntax (DeepSeek REPL-mimic fallback)

    iter 323ad — added shapes 2 & 3 to stop the "raw JSON dikh raha hai"
    bug where the parser missed unfenced emissions.
    iter 143 — added shape 4 to catch DeepSeek's occasional Python-style
    emissions like read_repo_file(path='x.py'); these were previously
    printed verbatim to the user instead of running the tool.
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

    if calls:
        return calls

    # Shape 4 — Python function-call syntax
    # Handles: read_repo_file(path='x') or
    #          tool_name(arg1='val', arg2=['a','b'])
    # ORA sometimes emits this when DeepSeek mimics
    # Python REPL style instead of JSON fence.
    import re as _re
    _PY_CALL_RE = _re.compile(
        r'(\w+)\s*\(\s*(.*?)\s*\)',
        _re.DOTALL
    )
    _KNOWN_TOOLS = {
        "read_repo_file", "read_repo_files", "write_repo_file",
        "get_repo_structure",
        "list_repo_files",
        "search_repo", "semantic_search_repo", "get_commit_diff",
        "get_repo_info", "find_usages", "get_dependencies",
        "get_env_vars", "detect_framework", "get_commit_history",
        "list_issues", "get_pr_comments", "find_package_docs",
        "validate_syntax", "e2b_run_code", "execute_bash",
        "web_search", "fetch_url", "web_search_and_summarize",
        "firecrawl_scrape", "firecrawl_crawl_site",
    }
    for match in _PY_CALL_RE.finditer(text):
        fn_name = match.group(1).strip()
        if fn_name not in _KNOWN_TOOLS:
            continue
        raw_args = match.group(2).strip()
        if not raw_args:
            calls.append({"tool": fn_name, "args": {}})
            continue
        # Parse keyword args: key='val' or key=["a","b"]
        args_dict = {}
        kw_re = _re.compile(
            r"(\w+)\s*=\s*("
            r"'[^']*'|\"[^\"]*\"|"     # single/double quoted string
            r"\[[^\]]*\]|"              # list
            r"\d+|True|False|None"      # primitives
            r")"
        )
        for kw in kw_re.finditer(raw_args):
            k = kw.group(1)
            v_raw = kw.group(2)
            try:
                import ast as _ast
                v = _ast.literal_eval(v_raw)
            except Exception:
                v = v_raw.strip("'\"")
            args_dict[k] = v
        if args_dict or not _re.search(r'\w+\s*=', raw_args):
            calls.append({"tool": fn_name, "args": args_dict})

    # iter 212l — Shape 5 (Natural-language tool extraction) was REMOVED.
    # It parsed phrases like "I need to read backend/auth.py" from the
    # LLM's own prose into phantom tool calls. Three problems:
    #   1. The phrase typically appeared in the model's FINAL answer
    #      (after it already had the data), so the phantom call
    #      consumed an extra iteration with a stale request.
    #   2. The extracted "path" was usually a bare filename without a
    #      directory prefix → 404 → wasted round-trip.
    #   3. The model couldn't disable it; even instructing it to
    #      "ignore my last sentence" still got the phantom call fired.
    # Shapes 1-4 cover every real emission format (fenced JSON, bare
    # JSON, OpenAI-style {tool_calls:[...]}, Python-style fn(a=b)).

    return calls


def strip_tool_calls(text: str) -> str:
    """Remove every shape of tool-call leakage from a model reply.

    Iter 35 introduced the original fenced-JSON stripper.
    Iter 212m-41 extended it to also drop XML fences, bare top-level
    JSON tool envelopes, and "Calling X with:" preambles after we
    observed Claude/GLM emitting all four shapes in production Ask
    Advisor turns where there's no orchestrator loop to consume them.
    """
    if not text:
        return text
    cleaned = _TOOL_CALL_RE.sub("", text)
    cleaned = _TOOL_CALL_XML_RE.sub("", cleaned)
    cleaned = _TOOL_CALL_BARE_JSON_RE.sub("\n", cleaned)
    cleaned = _TOOL_CALL_PREAMBLE_RE.sub("\n", cleaned)
    # Collapse runs of >2 blank lines that the strips might have left
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
