# Security & Trust — ORA by Aurem CTO
**Last updated: February 12, 2026**

Aurem CTO ships production code on your behalf. That means you're extending your **trust boundary** to us. This page documents — with engineering-grade specificity — how we protect that trust.

If you find a security issue, please report it to **security@auremcto.com**. We honour responsible disclosure and will credit you on our Hall of Fame if you consent.

---

## 1. Encryption

### 1.1 In Transit
- **TLS 1.2+** (TLS 1.3 preferred) on every connection between your browser, our backend, and every subprocessor.
- HSTS with `max-age=31536000; includeSubDomains; preload` enforced on `auremcto.com`.
- Modern cipher suites only (ECDHE + AEAD); RC4/3DES/CBC-only are disabled at the load balancer.

### 1.2 At Rest
- **MongoDB Atlas** encrypts all data at rest with AES-256 (managed by Atlas), plus encrypted backups.
- **GitHub Personal Access Tokens** are additionally wrapped with **HKDF-Fernet** using a per-tenant derived key so a bare database dump does not expose usable tokens.
- **Log files** on containers are ephemeral; long-term error logs (Sentry) have PII scrubbers on ingest.

### 1.3 Key Management
- Application secrets live in environment variables managed by the deployment platform (Emergent).
- **30-day rotation** policy for high-privilege credentials (LLM provider keys, Stripe keys, Sentry DSN).
- Root database credentials rotate on personnel change.

---

## 2. Token & Credential Handling

### 2.1 GitHub PATs
- We recommend **fine-grained** PATs scoped to only the repositories you want ORA to touch.
- Tokens are validated on ingress: we call `GET /user` once and reject any PAT with excessive scope beyond what we advertise.
- Tokens are never rendered in the UI after entry, never logged, never sent to LLMs, and never included in analytics events.
- You may revoke your PAT anytime from **[Settings → GitHub → Revoke access](/settings)** or from GitHub's own token page.

### 2.2 Session Tokens (JWT)
- Issued only after successful OAuth or password login.
- Signed with HS256 using a rotating secret.
- 7-day lifetime; refresh triggers a full token rotation.
- Stored in `HttpOnly; Secure; SameSite=Lax` cookies where the browser permits.

### 2.3 CSRF
- Non-idempotent endpoints validate a per-session `X-CSRF-Token` header.
- Same-Origin policy plus SameSite cookies provide defence-in-depth.

---

## 3. Authentication & Authorization

- **Password login:** bcrypt (cost factor 12) with per-user salt; no plaintext at any point.
- **OAuth:** GitHub identity + Emergent-managed Google OAuth. Redirect URIs are validated against an allow-list.
- **Rate limiting:** 5 failed logins/15 min per IP triggers exponential backoff.
- **Session revocation:** signing out invalidates the JWT server-side (revocation list checked on each auth-required request).

---

## 4. Isolation

- Each customer's data is scoped by `user_id` / `project_id` on every read + write. Database queries without one of these keys are rejected at the ORM boundary.
- Task execution runs in **E2B sandboxes** where required — file writes stay inside the ephemeral container.
- LLM calls include no other customer's context. Prompts are assembled per-task, per-user.

---

## 5. Secure Development

- **Static analysis:** Ruff (Python) + ESLint (JS/TS) required in CI.
- **Dependency scanning:** GitHub Dependabot enabled on our own repos.
- **Container hardening:** we run our own Docker CIS Benchmark scanner on our images (the same one we sell to you).
- **Secrets scanning pre-flight:** every LLM call is filtered through a detect-secrets pass to prevent accidental exfiltration of customer credentials.
- **Code review:** required before deploy to production.

---

## 6. Infrastructure

- **Hosting:** Emergent Labs (Kubernetes on GCP-backed infrastructure) — US region.
- **Database:** MongoDB Atlas — US multi-region cluster with automated snapshots (7-day point-in-time recovery).
- **CDN / Edge:** Vercel for static assets.
- **Monitoring:** Sentry (backend errors), Langfuse (LLM traces), Prometheus/Grafana (internal ops).

See the [Subprocessor List](/subprocessors) for the full inventory.

---

## 7. Incident Response

- **Detection:** anomaly alerts on auth failure rate, LLM cost spike, 5xx surge, database CPU.
- **Escalation:** on-call rotation via PagerDuty-style paging.
- **Communication:** we notify affected customers within **72 hours** of confirmed personal-data breaches, per GDPR Art. 33 and PIPEDA breach-reporting duties.
- **Post-mortem:** shared privately with affected enterprise customers; sanitised summary published on the [Status page](/status).

---

## 8. Reporting a Vulnerability

We welcome coordinated disclosure. Please:
1. Email **security@auremcto.com** with a description, steps to reproduce, and any PoC.
2. Give us **90 days** to remediate before public disclosure (shorter if actively exploited).
3. Do **not** test on other customers' accounts. Please use your own accounts and repos.

We do not currently run a paid bug-bounty programme, but critical findings earn public credit and — where permitted — swag.

**Out of scope:** DDoS, social engineering of staff, physical attacks, findings requiring privileged access already granted by the victim.

---

## 9. Compliance Roadmap

| Standard | Status | Target |
|---|---|---|
| GDPR / UK-GDPR | ✅ Aligned (see [DPA](/dpa)) | — |
| CCPA / CPRA | ✅ Aligned | — |
| DPDP Act (India) | ✅ Aligned | — |
| PIPEDA (Canada) | ✅ Aligned (home jurisdiction) | — |
| SOC 2 Type I | 🟡 Planned | Q4 2026 |
| ISO 27001 | 🟡 Planned | 2027 |

---

## 10. Data Portability

- Export all your data (tasks, projects, task history) at any time from **[Settings → Data → Export](/settings)**.
- Format: JSON.
- Deletion certificate available on request per [DPA §7](/dpa).

---

## 11. Contact

**Polaris Built Inc**
Incorporated in Canada
Security: **security@auremcto.com** (PGP key on request)
Privacy: **privacy@auremcto.com**
General: **ora@auremcto.com**
