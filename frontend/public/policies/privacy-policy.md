# Privacy Policy — ORA by Aurem CTO
**Last updated: June 16, 2026**
**Effective: June 16, 2026**

Polaris Built Inc ("we", "us") operates ORA by Aurem CTO at auremcto.com. This policy explains what data we collect, how we use it, and your rights. ORA is an AI engineer that reads your GitHub repo, writes production code, and commits directly — no IDE needed.

---

## 1. Data We Collect

### Account Data
- GitHub username, email address, avatar
- Subscription tier and billing status
- Account creation date and last login

### Project Data
- GitHub repository URLs you connect
- GitHub Personal Access Tokens (encrypted at rest with HKDF-Fernet)
- Repository file structure and selected file contents
- Task history and commit SHAs

### Usage Data
- Tasks submitted and completed
- AI model used per task (DeepSeek, Claude)
- Token usage per task
- Feature usage (Maxx mode on/off, parallel agents)

### Technical Data
- IP address (for rate limiting, stored max 30 days)
- Browser type and OS (for error diagnosis)
- Sentry error reports (anonymized stack traces)

### Payment Data
- Billing handled by Stripe — we store only: plan name, subscription status, Stripe customer ID
- We never store credit card numbers

### MCP API Keys (Iter 174)
When you mint an MCP API key (POST /api/aurem-dev/mcp/keys) for use with Claude Desktop, Cursor, or other MCP clients, we store:
- The full key (prefix `sk-aurem-…`) — required to validate inbound requests
- The `user_id` it belongs to, `created_at`, `active` flag, and `last_used_at` timestamp
- **No additional PII is attached** — the key is purely an auth token scoped to your existing account
- You can list and **revoke any key at any time** via the dashboard or `DELETE /api/aurem-dev/mcp/keys/{tail}`. Revoked keys are immediately rejected on every subsequent request.
- MCP tool calls (`list_projects`, `ship_code`, `get_task_status`, `get_recent_commits`) are logged identically to in-app actions for security audit purposes.

---

## 2. How We Use Data

| Data | Purpose |
|---|---|
| Email | Account login, billing receipts, important service notices |
| GitHub token | Reading/writing your repositories on your instruction |
| Repository content | Providing the coding assistant service |
| Usage data | Improving the Service, billing, abuse prevention |
| IP address | Rate limiting, security |
| Error data | Fixing bugs, improving reliability |

**We do NOT:**
- Sell your data to any third party
- Use your code to train AI models
- Share your data with other users
- Send marketing emails without consent (you can opt out anytime)

---

## 3. AI Processing

When you submit a task, relevant code from your repository is sent to AI providers:
- **DeepSeek V3** via OpenRouter — with `data_collection: deny` flag set
- **Claude Sonnet** via Anthropic (Maxx mode) — subject to Anthropic's privacy policy

We configure these providers to not use your data for training. However, you should not submit code containing passwords, private keys, or personal data of others.

---

## 4. Data Storage & Security

- **Location:** MongoDB Atlas (Canada/US), Emergent cloud infrastructure
- **GitHub tokens:** Encrypted at rest (HKDF-Fernet 256-bit)
- **Passwords:** Not stored — we use GitHub OAuth only
- **Transit:** TLS 1.2+ on all connections
- **Access:** Limited to authorized personnel only
- **Retention:** Account data kept until deletion request + 30 days

---

## 5. Third-Party Services

| Service | Purpose | Privacy Policy |
|---|---|---|
| GitHub | OAuth login, repository access | github.com/site/privacy |
| Stripe | Payment processing | stripe.com/privacy |
| OpenRouter/DeepSeek | AI code generation | openrouter.ai/privacy |
| Anthropic | Maxx mode AI review | anthropic.com/privacy |
| Sentry | Error tracking | sentry.io/privacy |
| Resend | Transactional email | resend.com/privacy |
| Firecrawl | Web research in tasks | firecrawl.dev/privacy |
| E2B | Sandboxed code execution | e2b.dev/privacy |

---

## 6. Cookies

We use minimal cookies:
- **Session cookie:** Keeps you logged in (essential, cannot be disabled)
- **Preference cookie:** Stores UI settings like dark mode
- No advertising or tracking cookies
- No third-party tracking pixels

---

## 7. Your Rights

Depending on your location, you may have the right to:

**All users:**
- Access your data (Settings → Export Data)
- Delete your account and data (Settings → Delete Account)
- Correct inaccurate data
- Opt out of non-essential emails

**EU/UK residents (GDPR):**
- Data portability
- Restrict processing
- Object to processing
- Lodge complaint with your supervisory authority

**California residents (CCPA):**
- Know what data we collect
- Delete personal information
- Opt out of sale (we do not sell data)
- Non-discrimination for exercising rights

**Canadian residents (PIPEDA):**
- Access your personal information
- Challenge accuracy of your data
- Withdraw consent (by deleting account)

To exercise any right: email **polarisbuiltinc@gmail.com** (support), or use Settings → Delete Account in the app.

---

## 8. Data Retention

| Data | Retention |
|---|---|
| Account data | Until account deletion + 30 days |
| Task history | 12 months rolling |
| GitHub tokens | Until project disconnected |
| Payment records | 7 years (legal requirement) |
| Error logs | 90 days |
| IP logs | 30 days |

---

## 9. Children's Privacy

The Service is not directed to persons under 18. We do not knowingly collect personal information from minors. If you believe a minor has provided us data, contact polarisbuiltinc@gmail.com.

---

## 10. Data Breach Notification

In the event of a data breach affecting your personal data, we will notify you within 72 hours of becoming aware, as required by GDPR Article 33 and applicable law.

---

## 11. International Transfers

Data may be processed in Canada, the United States, and other countries where our service providers operate. We ensure appropriate safeguards are in place for international transfers.

---

## 12. Changes to This Policy

We will notify you by email 14 days before material changes. The "Last updated" date at the top reflects the most recent revision.

---

## 13. Contact

**Privacy Officer**
Polaris Built Inc
polarisbuiltinc@gmail.com
auremcto.com

For GDPR requests, response within 30 days.
For CCPA requests, response within 45 days.

