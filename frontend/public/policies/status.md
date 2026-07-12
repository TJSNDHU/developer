# System Status — ORA by Aurem CTO
**Last updated: February 12, 2026**

This page reflects the current operational status of core ORA services. For real-time uptime and incident history, we are integrating a dedicated status platform (target: Q2 2026). Until then, this page and our email notifications are the source of truth.

If you believe a service is degraded and it's not reflected here, please email **support@auremcto.com** — we prefer to know from you than to hear about it later.

---

## 1. Current Status

| Component | Status | Notes |
|---|---|---|
| **Web app** (auremcto.com) | 🟢 Operational | React SPA on Vercel edge |
| **API backend** (`api.auremcto.com`) | 🟢 Operational | FastAPI + Kubernetes (Emergent) |
| **Database** (MongoDB Atlas) | 🟢 Operational | US multi-region cluster |
| **GitHub OAuth** | 🟢 Operational | Depends on GitHub upstream |
| **Fix pipeline (SSE)** | 🟢 Operational | Multi-agent Parliament router |
| **LLM Council A** (Claude Sonnet 4.6) | 🟢 Operational | Anthropic direct + OpenRouter |
| **LLM Council B** (DeepSeek) | 🟢 Operational | Direct API |
| **Fallback model** (GLM-5.2) | 🟡 Fallback | LongCat 2.0 upstream unavailable; auto-degrade active |
| **Payments** (Stripe) | 🟢 Operational | Live mode |
| **Email delivery** | 🟢 Operational | Google Workspace |

**Legend:** 🟢 Operational · 🟡 Degraded · 🔴 Outage · ⚫ Under maintenance

---

## 2. Recent Incidents

_No incidents reported in the last 30 days._

Historical incidents will appear here with:
- Start / end timestamps
- Affected components
- Root cause (post-mortem)
- Customer impact
- Remediation

---

## 3. Planned Maintenance

_No maintenance windows scheduled._

We give at least **48 hours' notice** for planned maintenance affecting availability, and prefer weekend low-traffic windows.

---

## 4. SLA & Availability Targets

For paid plans:

| Plan | Monthly Uptime Target |
|---|---|
| Free | Best effort |
| Solo | 99.5% |
| Team | 99.9% |
| Enterprise | 99.95% (custom SLA available) |

Uptime excludes:
- Planned maintenance (announced ≥ 48h in advance)
- Customer-caused outages (rate-limit exhaustion, invalid PATs)
- Upstream LLM provider outages (we route around them; residual downtime is force-majeure)
- GitHub / DNS / regional cloud-provider outages beyond our control

Missed-SLA credits are documented in our [Refund Policy](/refund-policy) §4.4 and per-plan Order Form.

---

## 5. Subscribe to Updates

- **Email notifications:** raise a support ticket at **support@auremcto.com** with subject "status subscribe" to receive incident notices.
- **RSS / dedicated status page:** planned Q2 2026 (StatusPage.io or equivalent).

---

## 6. Contact

**Polaris Built Inc**
Incorporated in Canada
Support: **support@auremcto.com**
Security incidents: **security@auremcto.com**
