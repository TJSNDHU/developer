# Subprocessor List — ORA by Aurem CTO
**Last updated: February 12, 2026**

Polaris Built Inc uses the third-party service providers ("subprocessors") listed below to deliver ORA by Aurem CTO. Each subprocessor is bound by a data-processing agreement (or equivalent contractual commitments) meeting GDPR Art. 28, CCPA/CPRA service-provider requirements, DPDP data-fiduciary duties, and PIPEDA safeguards.

We update this list whenever we add or remove a subprocessor. Material changes are announced by email **at least 30 days before** they take effect, giving enterprise customers the ability to object under Section 5 below.

---

## 1. Infrastructure & Hosting

| Subprocessor | Purpose | Location of Processing | Data Types |
|---|---|---|---|
| **Emergent Labs** | Kubernetes hosting, deploy pipeline | US | Application traffic, service logs |
| **MongoDB Atlas** | Primary application database (production) | US (multi-region) | User accounts, tasks, projects, encrypted PATs |
| **Vercel Inc.** | Static-asset hosting, deploy hooks | US / global edge | Public assets, marketing site |

---

## 2. AI / LLM Providers (see [AI Processing Disclosure](/ai-code-processing))

| Subprocessor | Purpose | Location | Data Types |
|---|---|---|---|
| **Anthropic PBC** | Council A — code generation (Claude Sonnet 4.5/4.6) | US | Selected file contents, task prompts |
| **OpenRouter** | LLM routing gateway | US | Same as above (proxied) |
| **DeepSeek** | Council B — validation | China / Global | Same as above |
| **Groq Cloud** | Low-latency utility inference | US | Same as above |
| **Zhipu AI (GLM)** | Fallback model, via OpenRouter | China (via OpenRouter US endpoint) | Same as above |

---

## 3. Source-Control Integration

| Subprocessor | Purpose | Location | Data Types |
|---|---|---|---|
| **GitHub Inc.** (Microsoft) | Repository access, OAuth identity | US | Username, email, avatar, repo contents (per your PAT scope) |

---

## 4. Payments & Billing

| Subprocessor | Purpose | Location | Data Types |
|---|---|---|---|
| **Stripe Inc.** | Payment processing, subscription management | US / global (customer-region routing) | Name, billing address, card token (Stripe holds full card data — we do not) |

---

## 5. Observability, Analytics & Support

| Subprocessor | Purpose | Location | Data Types |
|---|---|---|---|
| **Sentry (Functional Software Inc.)** | Backend error tracking (server-side only) | US | Stack traces, request context (PII scrubbed) |
| **Langfuse GmbH** | LLM observability (server-side only) | EU (us.cloud.langfuse.com endpoint served from US) | Prompt/response metadata, latency, tokens (opt-out of full content available) |
| **Meta Platforms Inc.** (Meta Pixel) | Marketing conversion tracking (browser) | US | IP, browser fingerprint, page views (consent-gated — see [Cookie Policy](/cookie-policy)) |
| **Google LLC** (Google Ads gtag) | Ad conversion tracking (browser) | US / global | IP, click ID, conversion events (consent-gated) |

---

## 6. Communication

| Subprocessor | Purpose | Location | Data Types |
|---|---|---|---|
| **Google Workspace** | Support & billing email (ora@ / billing@ / privacy@ / security@ auremcto.com) | Global | Support correspondence, account-related email |

---

## 7. Sandbox & Execution

| Subprocessor | Purpose | Location | Data Types |
|---|---|---|---|
| **E2B** | Isolated code execution sandbox for validation runs | US | Ephemeral file contents (destroyed with sandbox) |

---

## 8. Objection & Contact

**Enterprise customers** with a signed [DPA](/dpa) may object to a new subprocessor within **30 days of notice** by writing to **privacy@auremcto.com**. If we cannot resolve the objection with a reasonable alternative, you may terminate the affected service and receive a prorated refund.

**All users** may email questions to:
- Data-processing questions → **privacy@auremcto.com**
- Security concerns → **security@auremcto.com**

**Polaris Built Inc**
Incorporated in Canada
