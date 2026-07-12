# AUREM CTO — Simulated User QA
Directive: Integrate Promptfoo simulated-user for internal QA.

## Purpose
Run adversarial LLM personas against the **real** chat pipeline
(`routers/chat.py::chat_stream` → `services/orchestrator.chat_with_tools`
→ `services/tool_executor` → `core/parliament.py`) to catch bugs that
unit tests miss — silent tool-skip, wrong-project context, quota bypass,
concurrency issues.

## Reality-drift note
The directive named `intent_gateway.py` + `tool_router.py`. Live repo
uses `services/orchestrator.py::chat_with_tools` + `services/tool_executor.py`.
Suite adapts to real files.

## Ground rules baked in
- `PROMPTFOO_DISABLE_REMOTE_GENERATION=true` set in `.env.qa` (and the
  CI job env block). No outbound calls to Promptfoo Cloud.
- Suite is dev-only — not bundled into production frontend.
- Provider is `http` — hits the local FastAPI backend directly.
- No mocks. Every assertion runs against a live backend + a seeded
  test user in a throwaway MongoDB.

## Layout
```
qa/simulated-user/
├── promptfooconfig.yaml       # main config (5 scenarios + provider)
├── .env.qa                    # environment for the qa CLI
├── seed_qa_user.py            # creates the test account + project
├── run.sh                     # one-shot: seed → run promptfoo → exit code
└── README.md                  # this file
```

## Run locally
```bash
# 1. Ensure backend running on 127.0.0.1:8001 (supervisorctl status)
# 2. Install promptfoo (dev-only, once):
npm install -g promptfoo
# 3. Run
cd qa/simulated-user
bash run.sh
```

## CI
`.github/workflows/ci.yml` runs the suite in a new job
`simulated-user-qa` alongside `backend-tests`. `deploy-gate` requires
it to pass — same tier as ruff/eslint Verify.
