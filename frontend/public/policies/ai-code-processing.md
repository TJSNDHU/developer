# AI & Code Processing Disclosure — ORA by Aurem CTO
**Last updated: February 12, 2026**
**Effective: February 12, 2026**

ORA by Aurem CTO reads your GitHub repository, generates fixes with large language models (LLMs), and commits code directly. This document explains — in engineering-grade detail — exactly what happens to your code when it enters our system.

This disclosure supplements our [Privacy Policy](/privacy), [Subprocessor List](/subprocessors), and [Terms of Service](/terms).

---

## 1. What We Access

When you connect a GitHub repository:

| Data Type | Purpose | Retention |
|---|---|---|
| Repository metadata (name, default branch, languages, size) | Task routing & UI | Until repo disconnected |
| Selected file contents (specific files needed for the task) | Fed to LLM for context | Purged after task completion (see §4) |
| Diff/patch produced by the LLM | Preview & commit | Stored 90 days for undo/audit |
| Task inputs (your prompt/instruction) | Model input | Stored 30 days for support & abuse review |
| Task outputs (model response, commit SHA) | Task history & billing | Stored while your account is active |
| GitHub Personal Access Token (PAT) | Reading/writing to your repo | Encrypted at rest (HKDF-Fernet), scope-scoped |

We do **not** clone your entire repository to our servers. We fetch only the specific files an active task requires.

---

## 2. Where Your Code Goes (LLM Routing)

ORA uses a multi-model "Parliament" router. A single task may be evaluated by 2–3 models in parallel for consensus. The models currently in production:

| Model | Provider | Purpose | API Endpoint |
|---|---|---|---|
| Claude Sonnet 4.5 / 4.6 | Anthropic PBC (via OpenRouter or direct) | Council A — primary code generation | api.anthropic.com / openrouter.ai |
| DeepSeek Chat | DeepSeek | Council B — validation & lightweight fixes | api.deepseek.com |
| GLM-5.2 | Zhipu AI (via OpenRouter) | Fallback when LongCat unavailable | openrouter.ai |
| Groq-hosted models | Groq Cloud | Low-latency utility tasks | api.groq.com |

**Which model sees which code is deterministic per task type.** See our [Subprocessor List](/subprocessors) for full inventory.

### 2.1 Data Flow
```
Your Browser → Aurem CTO backend (Canada/US region) → LLM provider API
                                                    ↳ Response → Aurem CTO → GitHub commit
```

Every hop uses TLS 1.2+ encryption in transit.

---

## 3. Training & Model-Improvement Opt-Out

**Your code is never used to train LLMs.** We contractually enforce this in the following ways:

| Provider | Training Opt-Out Status | Evidence |
|---|---|---|
| **Anthropic** | ✅ Opted out via API Zero-Data-Retention default | [Anthropic API Data Usage](https://www.anthropic.com/legal/commercial-terms) |
| **OpenRouter** | ✅ We set `X-OR-Train: false` and use privacy-preserving routes | [OpenRouter Privacy](https://openrouter.ai/privacy) |
| **DeepSeek** | ✅ Enterprise API — no training on inputs | [DeepSeek Terms](https://platform.deepseek.com/terms) |
| **Groq** | ✅ Zero-retention API tier | [Groq Privacy](https://groq.com/privacy-policy/) |
| **Zhipu (GLM)** | ✅ Via OpenRouter — no training on API traffic | Contractual (OpenRouter) |

If any upstream provider changes their training policy, **we will notify you 30 days before continuing to route traffic to them**, and we will provide an opt-out.

---

## 4. Data Retention at LLM Providers

Even without training, some providers cache request/response pairs for abuse monitoring. We use the shortest retention available:

| Provider | Retention | Our Configuration |
|---|---|---|
| Anthropic | 30 days (abuse monitoring only) | Zero-Data-Retention enabled where eligible |
| OpenRouter | 30 days (opt-out available for paid tier) | Opt-out flag set |
| DeepSeek | 30 days | Standard API |
| Groq | 24 hours | Standard API |

**Aurem CTO's own retention:** Selected file contents are purged from our servers within **24 hours** of task completion. Only the diff, commit SHA, and task metadata are retained per §1.

---

## 5. What We Do NOT Do

- ❌ We do not sell your code or code-derived data.
- ❌ We do not use your code to train our own models (we don't train any models).
- ❌ We do not share your code with LLM providers for their own training.
- ❌ We do not access files outside the scope of an active task.
- ❌ We do not read repositories you have not explicitly connected.
- ❌ We do not persist your PAT in plaintext or in logs.

---

## 6. Sensitive-Data Detection

Before sending file contents to an LLM, we run a **pre-flight secret scan** using detect-secrets patterns to redact:
- API keys (AWS, Stripe, OpenAI, GitHub, etc.)
- Private keys (RSA, EC, PGP blocks)
- Database connection strings with credentials
- JWT tokens with recognisable structure

Redaction replaces the match with `[REDACTED_SECRET_<type>]` before the LLM sees it. This is best-effort — you remain responsible for what is committed to your repo.

---

## 7. Human Review

- **Your code is not read by Aurem CTO staff** except when you explicitly file a support ticket that requires it. Such reviews are logged, and you are notified.
- Anonymised/aggregated **task metadata** (model latency, error rates, no code content) is reviewed by engineering for reliability and cost optimisation.

---

## 8. Model Output Warranty

LLM output is probabilistic. Aurem CTO does **not** warrant that generated code is:
- Bug-free
- Optimal
- Free of licensing conflicts with third-party dependencies your project pulls in

**You are the final reviewer.** Every fix goes through a preview + commit step. We recommend keeping automated tests + code review in your workflow. See [Terms §7 — Limitation of Liability](/terms).

---

## 9. Rights & Requests

Under **GDPR, CCPA/CPRA, DPDP Act,** and **PIPEDA**, you may:
- Request a copy of the AI-processed history on your account
- Request deletion of your task history
- Request the specific LLM provider that handled a given task ID
- Withdraw consent and terminate LLM processing (this ends your ability to use ORA fixes but preserves your account)

Email **privacy@auremcto.com** with your account email and task ID(s).

---

## 10. Changes

Material changes to LLM routing, subprocessor list, or retention windows trigger email notice at least **30 days** before taking effect.

---

## 11. Contact

**Polaris Built Inc**
Incorporated in Canada
AI/data inquiries: **privacy@auremcto.com**
Engineering security: **security@auremcto.com**
