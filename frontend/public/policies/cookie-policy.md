# Cookie Policy — ORA by Aurem CTO
**Last updated: February 12, 2026**
**Effective: February 12, 2026**

Polaris Built Inc ("we", "us", "Aurem CTO") operates ORA at auremcto.com. This policy explains what cookies and similar tracking technologies we set in your browser, why, and how you can control them.

This policy layers on top of our [Privacy Policy](/privacy) and applies globally — including to visitors covered by **GDPR (EU/UK)**, **CCPA/CPRA (California)**, **DPDP Act (India)**, and **PIPEDA (Canada)**.

---

## 1. What Are Cookies?

Cookies are small text files placed on your device by websites you visit. Similar technologies (localStorage, sessionStorage, pixels, web beacons) achieve the same purpose. Throughout this policy, "cookies" refers to all of these.

We classify cookies into four buckets:

| Category | Purpose | Consent required? |
|---|---|---|
| **Strictly Necessary** | Login, session, CSRF, security | No (legitimate interest) |
| **Functional** | UI preferences (theme, sidebar state) | No (legitimate interest) |
| **Analytics** | Usage measurement, error diagnosis | Yes (opt-in for EU/UK/DPDP) |
| **Marketing** | Ad attribution, retargeting | Yes (opt-in for EU/UK/DPDP) |

---

## 2. Cookies We Set

### 2.1 Strictly Necessary (always active)

| Name | Provider | Purpose | Lifetime |
|---|---|---|---|
| `aurem_jwt` | Aurem CTO (first-party) | Auth session token | 7 days |
| `aurem_csrf` | Aurem CTO (first-party) | CSRF protection | Session |
| `aurem_ui_prefs` | Aurem CTO (localStorage) | Theme, sidebar, dismissed tips | Persistent (client-only) |

### 2.2 Analytics & Marketing (consent-gated)

| Name / Pixel | Provider | Purpose | Lifetime | Category |
|---|---|---|---|---|
| `_fbp`, `_fbc` | Meta Platforms Inc (Meta Pixel) | Ad conversion, retargeting | Up to 90 days | Marketing |
| `_ga`, `_gid`, `_gac_*` | Google LLC (Google Ads gtag) | Ad conversion tracking (AW-18239920865) | Up to 24 months | Marketing |

We do **not** currently use Google Analytics 4 for behavioural analytics — only Google Ads for conversion attribution. If that changes, we will update this table and re-request consent.

### 2.3 Third-Party Assets (no cookies, but external requests)

- **Google Fonts** (`fonts.googleapis.com`) — CSS + font files. No cookies set.
- **Sentry** — server-side only (backend error tracking). No browser cookies.
- **Langfuse** — server-side only (backend LLM observability). No browser cookies.

---

## 3. Your Choices & Controls

### 3.1 Consent Banner
On your first visit from a jurisdiction requiring opt-in consent (EU/UK/EEA, India under DPDP, or where required by CCPA opt-out), we present a consent banner. Options:

- **Accept all** — enables analytics + marketing cookies
- **Reject non-essential** — only strictly necessary + functional cookies load
- **Manage preferences** — toggle each category individually

Your choice is stored in `aurem_consent` (localStorage) for 6 months, after which we re-ask.

### 3.2 Change Your Mind Anytime
Visit **[/cookie-preferences](/cookie-preferences)** or click "Cookie preferences" in the footer to reopen the banner and update your selection.

### 3.3 Browser-Level Controls
All modern browsers let you block/clear cookies:
- [Chrome](https://support.google.com/chrome/answer/95647)
- [Firefox](https://support.mozilla.org/en-US/kb/enhanced-tracking-protection-firefox-desktop)
- [Safari](https://support.apple.com/guide/safari/manage-cookies-sfri11471/mac)
- [Edge](https://support.microsoft.com/en-us/microsoft-edge/delete-cookies-in-microsoft-edge-63947406-40ac-c3b8-57b9-2a946a29ae09)

Blocking strictly-necessary cookies will break login and core functionality.

### 3.4 Do Not Track / Global Privacy Control
We honour the **Global Privacy Control (GPC)** signal. If your browser sends `Sec-GPC: 1`, we treat that as an opt-out of "sale/share" under CCPA and non-essential cookies under GDPR/DPDP — no banner interaction required.

---

## 4. Regional Rights Summary

| Region | Legal basis | Your rights |
|---|---|---|
| **EU / UK / EEA (GDPR)** | Opt-in consent for non-essential | Withdraw consent, access, delete, port data |
| **California (CCPA/CPRA)** | Opt-out of "sale/share" | Do Not Sell/Share, GPC honoured, "Limit Use of Sensitive PI" |
| **India (DPDP Act)** | Opt-in consent | Withdraw consent, access, correction, erasure, grievance officer contact |
| **Canada (PIPEDA)** | Meaningful consent | Access, correction, complaint to OPC |
| **Rest of World** | Opt-out where feasible | Contact us to exercise equivalent rights |

Exercise any right by emailing **privacy@auremcto.com** or **ora@auremcto.com**.

---

## 5. Changes to This Policy

We update this policy when we add/remove trackers. Material changes trigger a fresh consent banner. Non-material updates are logged here with a new "Last updated" date.

---

## 6. Contact

**Polaris Built Inc**
Incorporated in Canada
Email: **ora@auremcto.com**
Privacy inquiries: **privacy@auremcto.com**
