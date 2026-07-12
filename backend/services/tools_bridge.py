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

# Iter 212m-192 — lenient variant with no closing-tag requirement.
# glm-5.2 has been observed emitting an opening `<tool_call>` fence
# with malformed body and no close, which the strict regex above
# leaves in place — surfacing raw `<tool_call>…` fragments to the
# user. This variant chews up to the next blank line, next XML tag,
# or end-of-string so the leak stops at a natural boundary.
_TOOL_CALL_XML_LOOSE_STRIP_RE = re.compile(
    r'<\s*(?:tool_call|function_call|tool|function)\b[^>]*>'
    r'.*?'
    r'(?=\n\s*\n|<\s*/?\s*(?:tool_call|function_call|tool|function)\b|$)',
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
    # Hoisted so both Shape-4 and Shape-6 (XML block below) share it
    # without redefining per-iteration.
    kw_re = _re.compile(
        r"(\w+)\s*=\s*("
        r"'[^']*'|\"[^\"]*\"|"     # single/double quoted string
        r"\[[^\]]*\]|"              # list
        r"\d+|True|False|None"      # primitives
        r")"
    )
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

    # Iter 212m-192 — Shape 6 (XML-fenced tool calls). GLM-5.2, invoked
    # as the Council A fallback when LongCat is unavailable, has been
    # observed emitting `<tool_call>read_repo_file)("README.md")` in
    # Ask Advisor turns. The stripper already knew about `<tool_call>`
    # fences (`_TOOL_CALL_XML_RE`) but the extractor did not — so the
    # user saw a healthy chat that quietly ran zero tools
    # (`tool_calls_run: 0`) and got "cannot access repo" style
    # responses even with a perfectly valid PAT. This shape is
    # deliberately lenient:
    #   • Accepts `<tool_call>…</tool_call>`, `<tool_call>…` (no close)
    #     and `<function_call>…</function_call>` variants.
    #   • Inner content is re-parsed through the JSON/Python parsers
    #     already defined above — so an XML-wrapped-JSON emission
    #     works too. If that fails we fall back to finding the first
    #     `_KNOWN_TOOLS` name inside the block; args become empty.
    _TOOL_CALL_XML_LOOSE_RE = re.compile(
        r'<\s*(?:tool_call|function_call|tool|function)\b[^>]*>'
        r'(.*?)'
        r'(?:</\s*(?:tool_call|function_call|tool|function)\s*>|$)',
        re.DOTALL | re.IGNORECASE,
    )
    xml_calls: list[dict] = []
    for m in _TOOL_CALL_XML_LOOSE_RE.finditer(text):
        inner = (m.group(1) or "").strip()
        if not inner:
            continue
        # 1. Try JSON envelope first.
        parsed = False
        try:
            data = json.loads(inner)
            if isinstance(data, dict):
                tool_name = data.get("tool") or data.get("name") or data.get("function")
                if isinstance(tool_name, str):
                    tool_args = (
                        data.get("args") or data.get("parameters")
                        or data.get("arguments") or {}
                    )
                    if isinstance(tool_args, str):
                        try: tool_args = json.loads(tool_args)
                        except json.JSONDecodeError: tool_args = {}
                    xml_calls.append({"tool": tool_name, "args": tool_args})
                    parsed = True
        except json.JSONDecodeError:
            pass
        if parsed:
            continue
        # 2. Try Python-style call inside the block, e.g.
        #    `read_repo_file(path="x")`. The `_PY_CALL_RE` requires
        #    `name(` — malformed shapes like `name)("x")` won't match,
        #    so also do a plain scan for a known tool name and any
        #    string literals to use as positional args.
        py_hit = False
        for pm in _PY_CALL_RE.finditer(inner):
            fn = pm.group(1).strip()
            if fn not in _KNOWN_TOOLS:
                continue
            raw = pm.group(2).strip()
            args_dict: dict = {}
            for kw in kw_re.finditer(raw):
                k, v_raw = kw.group(1), kw.group(2)
                try:
                    import ast as _ast
                    v = _ast.literal_eval(v_raw)
                except Exception:
                    v = v_raw.strip("'\"")
                args_dict[k] = v
            xml_calls.append({"tool": fn, "args": args_dict})
            py_hit = True
        if py_hit:
            continue
        # 3. Last-ditch: scan the block for the first known tool name
        #    and pull the first string literal as the positional arg.
        #    This is what saves the malformed
        #    `<tool_call>read_repo_file)("README.md")` case.
        fn_match = None
        for known in _KNOWN_TOOLS:
            if _re.search(rf'\b{_re.escape(known)}\b', inner):
                fn_match = known
                break
        if fn_match:
            lit = _re.search(r'''["']([^"']+)["']''', inner)
            args_dict = {}
            if lit:
                # Best-effort positional → most tools that take one
                # string arg name it `path` (files) or `query` (search).
                if fn_match in {"search_repo", "semantic_search_repo"}:
                    args_dict["query"] = lit.group(1)
                else:
                    args_dict["path"] = lit.group(1)
            xml_calls.append({"tool": fn_match, "args": args_dict})
    if xml_calls:
        # Dedupe against Shape-4 matches: the Shape-4 scan runs across
        # the whole text and will happen to catch valid Python-style
        # calls that are also inside an XML fence, so we filter out
        # duplicates that already landed in `calls`.
        seen = {(c["tool"], json.dumps(c.get("args", {}), sort_keys=True)) for c in calls}
        for xc in xml_calls:
            key = (xc["tool"], json.dumps(xc.get("args", {}), sort_keys=True))
            if key not in seen:
                calls.append(xc)
                seen.add(key)

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
    # Iter 212m-192 — catch orphaned `<tool_call>…` fences with no
    # close tag (observed from glm-5.2 fallback). The extractor already
    # ran the recovery path; here we simply hide the raw fragment from
    # the user-visible reply.
    cleaned = _TOOL_CALL_XML_LOOSE_STRIP_RE.sub("", cleaned)
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
