# AUREM CTO — Founder Launch Checklist & Unit Economics

> Live deep-scan of the codebase + real 2026 vendor pricing.
> Generated: Feb 2026 (Iter 92).
> Production domain: **https://auremcto.com**

---

## PART 1 — FOUNDER-SIDE PRODUCTION READINESS

### ✅ ALREADY CONFIGURED (in `/app/backend/.env`)

| Key | Status | Notes |
|---|---|---|
| `EMERGENT_LLM_KEY` | ✅ Live | `sk-emergent-…` for Claude Sonnet 4.5 (Maxx mode) + cap_for() |
| `OPENROUTER_API_KEY` | ✅ Live | DeepSeek V3 via OpenRouter — default LLM |
| `LLM_MODEL` | ✅ `deepseek/deepseek-chat` | Cheap, fast workhorse |
| `STRIPE_API_KEY` | ✅ `sk_live_51TKUU…` | Real LIVE Stripe key — verified via API |
| `STRIPE_WEBHOOK_SECRET` | ✅ `whsec_xKEO…` | Real, 40+ chars |
| `STRIPE_STARTER_PRICE_ID` | ✅ `price_1TfXg6…` | Verified live, $9 CAD/mo |
| `STRIPE_PRO_PRICE_ID` | ✅ `price_1TfXi5…` | Verified live, $19 CAD/mo |
| `STRIPE_TEAM_PRICE_ID` | ✅ `price_1TfXil…` | Verified live, $35 CAD/mo |
| `GITHUB_OAUTH_CLIENT_ID` | ✅ `Ov23liJOw6…` | OAuth App, verified live |
| `GITHUB_OAUTH_CLIENT_SECRET` | ✅ `f97e8b69…` | 40-char hex |
| `GITHUB_REDIRECT_URI` | ✅ Set | `https://auremcto.com/api/aurem-dev/github/oauth/callback` |
| `TAVILY_API_KEY` | ✅ `tvly-dev-33X71D…` | Web search, ORA web-skill |
| `FIRECRAWL_API_KEY` | ✅ `fc-b13b99…` | Paid plan ACTIVE (verified live, HTTP 200) |
| `MONGO_URL` | ✅ Local | Production MongoDB connection |
| `DB_NAME` | ✅ `aurem_dev` | |
| `JWT_SECRET` | ✅ 64+ chars | Random, production-grade |
| `AUREM_MASTER_KEY` | ✅ Fernet key | Encrypts vault tokens |
| `ORA_API_KEY` / `ORA_BASE_URL` | ✅ | Upstream ORA service |
| `ADMIN_EMAIL` / `FOUNDER_EMAILS` | ✅ | `teji.ss1986@gmail.com`, `test@aurem.dev` |
| `APP_URL` | ✅ `https://aurem.dev` | Used for redirects post-OAuth |
| `CORS_ORIGINS` | ✅ `*` | Locked open — fine for SaaS, tighten if needed |

### ❌ MISSING / EMPTY — Decide if you need these before launch

| Key | Impact if Missing | When you'd need it |
|---|---|---|
| `GITHUB_TOKEN` | Falls back to user's OAuth token (fine for solo dev) | Only if you want a SHARED bot account to push to all client repos |
| `GITHUB_ORG` | Project listing isn't org-scoped | Only if you operate as an org and want org-only repos |
| `E2B_API_KEY` | Sandbox `run_code` skill no-ops silently | Required for ORA to actually run Python/JS in a sandbox before shipping |
| `VERCEL_API_TOKEN` | "Deploy to Vercel" button shows 503 | Required if you want one-click Vercel deploys from chat |
| `FRONTEND_URL` | Falls back to request origin (works but messy logs) | Hard-pin to `https://auremcto.com` for clean Stripe redirect URLs |
| `SENTRY_DSN` | No error monitoring | Optional but **highly recommended** for prod — catches silent crashes |

**Recommendation:** Get **E2B + Vercel + Sentry** before public launch. Without E2B, ORA can't truly "test before ship" — it'll just write code and hope. That's the biggest competitor differentiator gap right now.

### 🔧 STRIPE DASHBOARD ACTIONS (one-time setup on stripe.com)

1. **Webhook endpoint** → Developers → Webhooks → Add endpoint:
   - URL: `https://auremcto.com/api/aurem-dev/payments/webhook`
   - Events: `checkout.session.completed`, `customer.subscription.deleted`, `customer.subscription.paused`
   - ✅ `STRIPE_WEBHOOK_SECRET` already set, so this is just config on Stripe's side.
2. **Billing Portal** → Settings → Billing → Customer portal → Activate (so `/payments/portal` endpoint works for cancellations).
3. **Tax** → Stripe Tax → Activate if selling globally (Canada GST/HST auto-collection).
4. **Promotion codes** → Already enabled in checkout code (`allow_promotion_codes=True`). Create your launch coupon at Products → Coupons.

### 🔧 GITHUB OAUTH APP DASHBOARD (verify)

- Authorization callback URL: `https://auremcto.com/api/aurem-dev/github/oauth/callback` ✅
- Homepage URL: `https://auremcto.com`
- Enable Device Flow: ❌ (not used)
- Request user authorization (OAuth) during installation: ✅ if you ever convert to GitHub App

### 🌐 DNS / DOMAIN (production)

- `auremcto.com` already pointed at Emergent's prod IPs (confirmed live).
- SSL: Auto-managed by Emergent. No action needed.
- If you ever migrate hosting, you'll need to re-do these.

### 🚨 PROD-ONLY ITEMS (this preview env ≠ production env)

The Emergent preview `.env` here is **independent** from `auremcto.com`'s production env vars. You must mirror these to prod via Emergent dashboard:

1. All 4 Stripe values (API key + 3 price IDs + webhook secret)
2. Both GitHub OAuth values (client_id + secret)
3. Tavily + Firecrawl keys
4. `EMERGENT_LLM_KEY`, `OPENROUTER_API_KEY`
5. `JWT_SECRET`, `AUREM_MASTER_KEY` (regenerate fresh for prod!)
6. `MONGO_URL` → production MongoDB Atlas connection string (NOT localhost)

⚠️ **Critical:** `JWT_SECRET` and `AUREM_MASTER_KEY` should be DIFFERENT in prod vs preview. If they're the same, anyone with the preview secret can forge prod tokens.

---

## PART 2 — UNIT ECONOMICS (real 2026 vendor pricing)

### 📊 VENDOR COST INPUTS (verified via web search Feb 2026)

| Service | Rate | Source |
|---|---|---|
| **DeepSeek V3** (OpenRouter, default chat) | $0.20/M input, $0.80/M output | openrouter.ai/deepseek/deepseek-chat |
| **Claude Sonnet 4.5** (Emergent, Maxx mode) | $3.00/M input, $15.00/M output | platform.claude.com/docs/pricing |
| **Tavily Basic search** | $0.008/search | docs.tavily.com/api-credits |
| **Tavily Advanced search** | $0.016/search | same |
| **Firecrawl scrape** (Standard plan) | ~$0.00083/page | firecrawl.dev/pricing |
| **E2B Sandbox** (1 vCPU, hobby) | ~$0.05/hour | e2b.dev/pricing |
| **Stripe transaction fee** | 2.9% + $0.30 CAD | stripe.com/ca/pricing |

### 💰 TIER PRICING (current state)

| Plan | Stripe Price | USD equivalent (FX 0.73) | Stripe fee | **Net revenue/user/mo** |
|---|---|---|---|---|
| Free | $0 | $0 | — | $0 (loss-leader) |
| Starter | $9 CAD | $6.57 | -$0.56 CAD | **$8.44 CAD = $6.16 USD** |
| Pro | $19 CAD | $13.87 | -$0.85 CAD | **$18.15 CAD = $13.25 USD** |
| Team | $35 CAD | $25.55 | -$1.32 CAD | **$33.68 CAD = $24.59 USD** |

### ⚙️ TOKEN COST PER UNIT OF WORK (estimates from code's `cap_for()` limits)

Per **typical task** (clone repo → orchestrator 3 rounds → code patch → push):
- Input: ~15K tokens (system prompt + repo context + history)
- Output: ~5K tokens (caps: chat=1500, code=3500, review=4096)

| Mode | Cost per task |
|---|---|
| **DeepSeek (chat mode)** | 15K × $0.0002 + 5K × $0.0008 = **$0.007/task** |
| **Claude Sonnet (code/review = Maxx mode)** | 15K × $0.003 + 5K × $0.015 = **$0.120/task** |

**Maxx mode is ~17x more expensive than default.** This is the single biggest cost driver.

### 👥 USER PROFILES (3 realistic scenarios per tier)

#### 🟢 STARTER ($9 CAD = $6.57 USD, 50 tasks/mo, NO Maxx — DeepSeek only)

| Cost item | Light user | Avg user | Heavy user |
|---|---|---|---|
| LLM tasks (50 × $0.007) | $0.18 | $0.35 | $0.35 |
| Chat turns (~80) | $0.10 | $0.20 | $0.30 |
| Tavily searches | $0.04 (5) | $0.08 (10) | $0.16 (20) |
| Firecrawl scrapes | $0.001 (1) | $0.004 (5) | $0.01 (12) |
| E2B sandbox time | $0.001 (1 min) | $0.005 (6 min) | $0.02 (24 min) |
| **Total cost (USD)** | **$0.32** | **$0.64** | **$0.85** |
| Net revenue | $6.16 | $6.16 | $6.16 |
| **Margin** | **$5.84 (95%)** | **$5.52 (90%)** | **$5.31 (86%)** 🟢 |

**Verdict:** Starter tier is **highly profitable**. Even the heaviest user keeps 86% margin. No risk.

#### 🟡 PRO ($19 CAD = $13.87 USD, unlimited tasks + Maxx mode)

| Cost item | Light user (40 tasks, 20% Maxx) | Avg user (100 tasks, 50% Maxx) | Heavy user (300 tasks, 80% Maxx) |
|---|---|---|---|
| LLM tasks | (8 × $0.12) + (32 × $0.007) = $1.18 | (50 × $0.12) + (50 × $0.007) = $6.35 | (240 × $0.12) + (60 × $0.007) = $29.22 |
| Chat turns | $0.30 | $0.75 | $2.00 |
| Tavily | $0.16 (20) | $0.40 (50) | $0.80 (100) |
| Firecrawl | $0.01 (10) | $0.04 (50) | $0.08 (100) |
| E2B (5/30/120 min) | $0.004 | $0.025 | $0.10 |
| **Total cost (USD)** | **$1.65** | **$7.57** | **$32.20** |
| Net revenue | $13.25 | $13.25 | $13.25 |
| **Margin** | **$11.60 (88%) 🟢** | **$5.68 (43%) 🟡** | **-$18.95 (-143%) 🔴 LOSS** |

**Verdict:** Pro tier is **profitable for 95% of users** but **the top 5% (heavy Maxx-mode hammerers) WILL bleed you dry.** One heavy abuser cancels out the profit from 4 average users.

**Mitigations to bake in:**
1. **Maxx-mode soft cap** — e.g., 100 Maxx tasks/mo before falling back to DeepSeek (auto-revert with toast notification).
2. **Fair-use clause** in ToS — reserve right to throttle abuse.
3. **Already implemented:** `services/usage.py` has token tracking — just enforce a Maxx-mode limit.

#### 🔴 TEAM ($35 CAD = $25.55 USD, unlimited + priority queue + parallel agents)

Team tier assumes 3+ active users per subscription. Real-world usage:

| Scenario | Active users on subscription | Combined monthly cost | Margin |
|---|---|---|---|
| **Solo founder using Team for "moral support"** (1 active, low usage) | 1 | $1.65 | **$22.94 (93%) 🟢** |
| **Small team, 3 avg users** | 3 × $7.57 = $22.71 | $22.71 | **$1.88 (8%) 🟡** |
| **Healthy team, 3 avg users + 1 heavy** | $22.71 + $32.20 = $54.91 | $54.91 | **-$30.32 🔴 LOSS** |
| **Enterprise abuser, 5 heavy Maxx users** | 5 × $32.20 = $161.00 | $161.00 | **-$136.41 🔴 CATASTROPHIC** |

**Verdict:** Team tier is **structurally underpriced at $35 CAD**. Once 3+ real engineers share it, costs eat the margin. Once a heavy user is on it, you lose money every month.

**Mitigations:**
1. 🔴 **Raise Team to $69-99 CAD/mo** (most common SaaS B2B price point for "team of 3-5 with full features"). Even GitHub Copilot Business is $19/user/mo = $57+ for a team of 3.
2. 🔴 **Per-seat pricing** instead of flat $35 — charge $19/user/mo with a 3-seat minimum = $57/mo floor.
3. 🟡 **Hard cap Maxx-mode at 200 tasks/seat/month** with overage at $0.50/task.
4. 🟡 **Priority queue is already Team-only** — keep this as the headline differentiator, don't dilute.

### 🎯 BREAK-EVEN ANALYSIS

Assuming fixed monthly overhead = **$200 USD/mo** (Vercel Pro $20 + MongoDB Atlas M10 $60 + Tavily monthly $30 + Firecrawl Standard $83 + misc):

| Tier | Margin/user | Users needed for break-even |
|---|---|---|
| Starter (avg) | $5.52 | **36 users** |
| Pro (avg) | $5.68 | **35 users** |
| Team (avg, 3-user) | $1.88 | **107 subs (320 active engineers)** 🔴 |
| **Mixed (1:2:1 ratio)** | ~$4.40 blended | **45 users** |

**Realistic launch goal:** Get to **50 paid users in 90 days** = profitable. Beyond that, every dollar is margin.

### 📈 PROJECTED P&L (12-month rolling, conservative)

Assumptions:
- Month 1: 10 paid users (50/30/20 split Starter/Pro/Team)
- Month 6: 100 paid users
- Month 12: 500 paid users
- Churn: 5%/mo
- Free-tier active users: 10x paid count (free is acquisition funnel)
- Free tier cost: ~$0.40/mo each (10 tasks DeepSeek only)

| Month | Paid users | MRR (USD) | Vendor costs (USD) | Fixed (USD) | **Net (USD)** |
|---|---|---|---|---|---|
| 1 | 10 | $116 | $50 (incl. free tier) | $200 | **-$134 🔴** |
| 3 | 30 | $349 | $150 | $200 | **-$1** |
| 6 | 100 | $1,162 | $500 | $250 | **+$412 🟢** |
| 12 | 500 | $5,810 | $2,500 | $400 | **+$2,910 🟢** |

⚠️ **Cash crunch zone is months 1-3.** Pre-fund $500-1000 to cover negative net before break-even.

### 🚨 TOP 3 ACTION ITEMS (founder, do this week)

1. 🔴 **RAISE TEAM TIER PRICE** from $35 CAD to **$69 CAD/mo** (or move to per-seat). Current price will bleed cash the moment teams adopt.
2. 🔴 **ADD MAXX-MODE QUOTA** to Pro tier (e.g., 100 Maxx tasks/mo, fallback to DeepSeek after). One line of code in `services/usage.py`.
3. 🟡 **SWITCH PRICES TO USD** — your customers will assume USD because $ is shown. Convert: Starter $9 USD, Pro $19 USD, Team $69 USD. Worth ~37% more revenue per sale.

### 💡 NICE-TO-HAVE OPTIMIZATIONS

- **Annual plans @ 20% discount** = lower churn, upfront cash. Stripe supports this out-of-box.
- **Prompt caching on Claude** (Anthropic offers 90% discount on cached input = $0.30/M instead of $3.00/M). Can cut Maxx-mode cost by ~70% if you cache system prompts.
- **Switch heavy Firecrawl scrapes to Tavily extract** when full-page render isn't needed (Tavily extract is ~$0.005/page vs Firecrawl $0.00083/page — actually Firecrawl is cheaper, so this advice is reversed for SaaS scale, OK keep Firecrawl).

---

## SUMMARY — IS AUREM CTO LAUNCH-READY?

**🟢 YES, for soft launch (friends/beta), assuming you:**
- Mirror env vars to prod dashboard and redeploy (15-min task).
- Activate Stripe Customer Portal + Webhook URL on Stripe dashboard.
- Have $500-1000 cash buffer for first 3 months.

**🟡 BEFORE PUBLIC LAUNCH:** 
- Add E2B_API_KEY (ORA can't truly test code without it).
- Add SENTRY_DSN (no observability = silent prod fires).
- Switch to USD pricing OR clearly label "CAD" everywhere.

**🔴 BEFORE SCALING PAST 100 USERS:**
- Raise Team tier to $69+ CAD.
- Cap Pro-tier Maxx mode at 100 tasks/mo.
- Move MongoDB to Atlas M10 ($60/mo) with backups.
- Add annual-plan SKUs for retention.

End of report.
