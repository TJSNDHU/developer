# Post-Launch Roadmap — Smart Model Router + Multi-Agent System

**Status:** NOT STARTED — Locked behind production launch.
**Estimated effort:** 3-4 days minimum.
**Do NOT start until:** auremcto.com is live, stable, and revenue is flowing.

---

## Why this is post-launch

Current setup works. Touching orchestrator.py + llm.py during launch =
high risk of breaking the very pipeline that's about to be tested by
real paying users. Wait until:
- ≥ 7 days of stable production traffic
- No P0/P1 customer-reported bugs
- Stripe revenue confirmed (at least one paid subscription)

Then ship this as a clearly-versioned Iter 165+ rollout behind a
feature flag (`SMART_AGENT_ROUTER=1`).

---

## What this delivers

Replaces the current single-model orchestrator flow with specialised
agents that route per (task_type, mode):

| Mode | Task | Model | Cost (input) |
|------|------|-------|--------------|
| All | Read files | `moonshotai/kimi-k2` | $0.57/M |
| Swift | Write code | `moonshotai/kimi-k2.7-code` | $0.75/M |
| Swift | Review | `moonshotai/kimi-k2.5` | $0.375/M |
| Pro | Write code | `moonshotai/kimi-k2.7-code` | $0.75/M |
| Pro | Review | `moonshotai/kimi-k2-thinking` | $0.60/M |
| Maxx | Write code | `anthropic/claude-sonnet-4-5-20250929` | $3/M |
| Maxx | Review | `moonshotai/kimi-k2-thinking` | $0.60/M |
| All | Security | `anthropic/claude-sonnet-4-5-20250929` | $3/M |
| Fallback | Anything fails | `deepseek/deepseek-chat` | — |

### Estimated per-task cost
- Swift ~$0.04
- Pro ~$0.045
- Maxx ~$0.085

### Agent classes to build
- `ReaderAgent` — reads repo files (Kimi K2)
- `CoderAgent` — writes code (mode-aware model)
- `ReviewerAgent` — reviews code (diff format, cheap)
- `SecurityAgent` — security scan (always Claude)
- `CoordinatorAgent` — orchestrates all agents, parallel where possible

### Per-mode flow

**SWIFT**
1. Read relevant files (ReaderAgent)
2. Write code (CoderAgent — Kimi K2.7)
3. Quick diff review (ReviewerAgent — Kimi K2.5)
4. Security scan (SecurityAgent — Claude) — parallel with review
→ Ship if clean

**PRO**
1. Read files (ReaderAgent)
2. Write code (CoderAgent — Kimi K2.7)
3. Deep review (Kimi Thinking) + Security (Claude) — parallel
→ Ship if clean

**MAXX**
1. Read files (ReaderAgent — cheap)
2. Write code (CoderAgent — Claude Sonnet)
3. Security scan (SecurityAgent — Claude) — no re-review needed
→ Ship

---

## Implementation Steps (locked spec)

### STEP 1 — UNDERSTAND WHAT EXISTS

```bash
cat /app/backend/services/llm.py
cat /app/backend/services/orchestrator.py | grep -n "swift_diff_review\|pro_parallel\|_call_claude\|_call_deepseek\|use_code_model" | head -40
```

### STEP 2 — Create `services/smart_router.py`

Single source of truth for model selection. Pure function module.

```python
"""
services/smart_router.py

Single source of truth for model selection.
Maps (task_type, mode) → model ID on OpenRouter.
"""
from __future__ import annotations
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

MODELS = {
    "read":              "moonshotai/kimi-k2",
    "swift_code":        "moonshotai/kimi-k2.7-code",
    "swift_review":      "moonshotai/kimi-k2.5",
    "pro_code":          "moonshotai/kimi-k2.7-code",
    "pro_review":        "moonshotai/kimi-k2-thinking",
    "maxx_code":         "anthropic/claude-sonnet-4-5-20250929",
    "maxx_review":       "moonshotai/kimi-k2-thinking",
    "security":          "anthropic/claude-sonnet-4-5-20250929",
    "fallback":          "deepseek/deepseek-chat",
}

TOKEN_BUDGETS = {
    "read":         1000,
    "swift_code":   3500,
    "swift_review":  400,
    "pro_code":     3500,
    "pro_review":   2000,
    "maxx_code":    4000,
    "maxx_review":  2000,
    "security":     1000,
    "fallback":     3500,
}

def get_model(task: str, mode: str = "swift") -> str:
    if task == "security":
        return MODELS["security"]
    if task == "read":
        return MODELS["read"]
    key = f"{mode}_{task}"
    model = MODELS.get(key)
    if not model:
        logger.warning(f"No model for task={task} mode={mode}, using fallback")
        return MODELS["fallback"]
    return model

def get_budget(task: str, mode: str = "swift") -> int:
    key = f"{mode}_{task}"
    return TOKEN_BUDGETS.get(key, TOKEN_BUDGETS.get(task, 3500))

def get_provider_name(task: str, mode: str = "swift") -> str:
    model = get_model(task, mode)
    if "claude" in model:        return "Claude Sonnet"
    if "kimi-k2.7" in model:     return "Kimi K2.7"
    if "kimi-k2-thinking" in model: return "Kimi Thinking"
    if "kimi-k2.5" in model:     return "Kimi K2.5"
    if "kimi-k2" in model:       return "Kimi K2"
    if "deepseek" in model:      return "DeepSeek V3"
    return model.split("/")[-1]
```

### STEP 3 — Create `services/agents.py`

(See original spec in chat history — 5 agent classes:
ReaderAgent, CoderAgent, ReviewerAgent, SecurityAgent, CoordinatorAgent.
CoordinatorAgent uses `asyncio.gather` to run review + security in parallel.)

### STEP 4 — Update `services/orchestrator.py`

- REMOVE: `_swift_diff_review`, `_pro_parallel_review`
- ADD: `from .agents import CoordinatorAgent`
- REPLACE the review tail (~lines 1358-1362) to call `CoordinatorAgent(mode=mode).run(...)`
- Keep `use_code_model` flag — CoordinatorAgent handles internal model selection

### STEP 5 — Update `services/llm.py`

Add generic OpenRouter caller `call_openrouter_model(model, system, user, max_tokens, temperature)`
for non-Claude / non-DeepSeek model routes. Then in `agents.py`'s `_call_model`,
delegate to it instead of duplicating httpx code.

### STEP 6 — Update `README.md` Architecture section

Add "Agent Architecture (Iter 165+)" subsection with the routing table above.

---

## Verification (run after STEPs 1-6)

```bash
python3 -c "
import ast, pathlib, sys
sys.path.insert(0, '/app/backend')

# smart_router.py
from services.smart_router import get_model, get_budget, get_provider_name
assert get_model('code', 'swift') == 'moonshotai/kimi-k2.7-code'
assert get_model('code', 'maxx') == 'anthropic/claude-sonnet-4-5-20250929'
assert get_model('security', 'swift') == 'anthropic/claude-sonnet-4-5-20250929'
assert get_model('read') == 'moonshotai/kimi-k2'
print('✅ smart_router: model mapping correct')

content = pathlib.Path('/app/backend/services/agents.py').read_text()
assert all(c in content for c in ['ReaderAgent','CoderAgent','ReviewerAgent','SecurityAgent','CoordinatorAgent'])
assert 'asyncio.gather' in content
print('✅ agents.py: all 5 agents exist, parallel gather present')

orch = pathlib.Path('/app/backend/services/orchestrator.py').read_text()
assert '_swift_diff_review' not in orch
assert '_pro_parallel_review' not in orch
assert 'CoordinatorAgent' in orch
print('✅ orchestrator: old review functions removed, agents wired')

llm = pathlib.Path('/app/backend/services/llm.py').read_text()
assert 'call_openrouter_model' in llm
print('✅ llm.py: call_openrouter_model added')

for f in ['services/smart_router.py','services/agents.py','services/orchestrator.py','services/llm.py']:
    ast.parse(pathlib.Path(f'/app/backend/{f}').read_text())
print('ALL CHECKS PASSED')
"

supervisorctl restart backend
sleep 3
curl -s http://localhost:8001/api/health | python3 -m json.tool
```

---

## Rollout plan

1. **Feature-flag everything**: `os.getenv("SMART_AGENT_ROUTER","0") == "1"`
2. **Shadow mode first**: Run new pipeline in parallel for 24h, log results, don't surface to users
3. **5% canary**: Enable for 5% of paid users, monitor cost + latency + error rate for 48h
4. **50% rollout**: If canary clean, expand to 50%, monitor 24h
5. **100%**: Full cutover, keep old code path behind `LEGACY_REVIEW_PATH=1` for 1 week
6. **Cleanup**: Remove old `_swift_diff_review` / `_pro_parallel_review` code after week 2

## Why we wait

- Today's customers haven't paid for this yet — risk:reward is wrong
- 3-4 days of focused engineering = 3-4 days of NO launch features shipped
- Smart routing is invisible to most users; new UI features drive signups
- After launch, when we have actual cost data per mode, we'll know if these specific model picks are right (they may not be)

---

## File: `/app/memory/post_launch_smart_router.md`
This is the canonical reference. Do not paste this prompt into a chat
when the time comes — open this file and execute STEP 1 through 6 in order.
