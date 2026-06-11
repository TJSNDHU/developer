"""
backend/evals — Eval-as-CI battery + adversarial security suite.

Run from the deploy gate:
    python -m backend.evals.runner
or:
    python backend/scripts/run_evals.py

Exit code 0 = all gates pass. Exit 1 = HARD-FAIL (leak / hallucination /
scope bypass / refusal bypass). Soft warnings do NOT block the deploy.

Modules:
  harness.py            — reusable Opik-style scorers (pure logic, no LLM)
  prompts_quality.py    — System 1: 12 quality prompts (inventory, chain,
                           identity, grounding)
  prompts_security.py   — System 2: 10 adversarial attack prompts
  runner.py             — orchestrates both batteries, writes JSON report
"""
