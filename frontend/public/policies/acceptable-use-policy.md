> ## ⚠️ DRAFT — Pending Legal Review
>
> **This document is a starting-point draft and has NOT been reviewed by a lawyer.** It is published here so users can see the intent, but it is not yet the final policy. Placeholders shown in `[BRACKETS]` will be filled in and legal review will be completed before this policy becomes binding.
>
> Questions or feedback? Email **[ora@auremcto.com](mailto:ora@auremcto.com)**.

---

# Acceptable Use Policy

**Last updated:** `[DATE — pending finalization]`

## 2.1 Why this policy exists

AUREM provisions **real infrastructure on your behalf** — GitHub repositories under an AUREM-owned organization, deployments on AUREM's Vercel account, and (for paid users) dedicated Supabase database projects. Because this infrastructure is owned and paid for by AUREM, not you, we need clear rules about what may be built and hosted through the platform.

## 2.2 Prohibited uses

You may not use AUREM to build, generate, host, or deploy applications that:

- **Facilitate illegal activity**, including but not limited to fraud, phishing, identity theft, sale of illegal goods/services, or circumvention of law enforcement.
- **Impersonate** any person, brand, or organization without authorization, or that recreate the login/branding of a real service in a way designed to deceive (phishing clones).
- **Distribute malware**, exploit code, credential-harvesting scripts, spam tooling, or any code whose primary purpose is to attack, disrupt, or gain unauthorized access to other systems.
- **Violate intellectual property rights** — including generating an app that substantially reproduces another company's copyrighted app, trademark, or proprietary design without a license.
- **Host sexual content involving minors, non-consensual intimate imagery, or content sexualizing minors in any form** — zero tolerance, immediate termination and, where required by law, reporting to relevant authorities.
- **Promote or facilitate violence, terrorism, or hate speech** directed at individuals or groups based on protected characteristics.
- **Scrape, harvest, or process personal data at scale** in violation of applicable privacy law (e.g. GDPR, CCPA), or build applications whose primary purpose is unauthorized surveillance or stalking of individuals.
- **Abuse shared infrastructure** — including excessive resource consumption designed to degrade service for other users, attempts to bypass rate limits/quotas, or automated mass-creation of projects/accounts.
- **Process regulated data without authorization** — e.g. building a healthcare app that stores real patient data (PHI) or a fintech app that stores real payment card numbers, without first confirming with us that the underlying infrastructure meets the relevant compliance requirement (HIPAA, PCI-DSS, etc.) for that use case. AUREM's default shared/free-tier infrastructure is **not certified** for regulated data.
- **Resell or white-label AUREM's infrastructure itself** (e.g. building a competing "app builder" on top of AUREM-provisioned repos/deployments) without a separate commercial agreement.

## 2.3 Ownership and control

- Applications generated under the Personal Track are created in an AUREM-owned GitHub organization and deployment accounts. AUREM reserves the right to inspect, suspend, or remove any generated application at any time if it reasonably believes Section 2.2 has been violated, without prior notice in cases of urgent risk (e.g. active phishing, malware).
- For non-urgent violations, we will attempt to notify you and provide an opportunity to remediate before suspension, except for repeat violations.
- You may request to migrate ownership of a compliant application to your own GitHub/Vercel/Supabase accounts at any time — see `[migration/export documentation]`.

## 2.4 Monitoring

We may use automated scanning (e.g. static code analysis, the platform's existing security-scanning pipeline) to detect policy violations in generated code. We do **not** proactively read the contents of your private application data (e.g. rows in your app's database) except:

1. In response to a valid legal request,
2. To investigate a specific abuse report, or
3. As strictly necessary to diagnose a platform-wide incident.

## 2.5 Consequences of violation

Depending on severity, consequences may escalate through the following stages:

1. Warning and remediation window
2. Feature restriction
3. Project suspension
4. Account termination without refund (see Refund Policy §1.6)
5. Law enforcement referral where legally required (e.g. CSAM, credible threats of violence)

## 2.6 Reporting abuse

If you believe an application built on AUREM violates this policy, report it to **[ora@auremcto.com](mailto:ora@auremcto.com)** with subject line "Abuse Report". We aim to review reports within `[X business days]`, sooner for urgent safety issues.

## 2.7 Changes to this policy

We may update this policy from time to time. Material changes will be notified via `[email/in-app notice]` at least `[14]` days before taking effect, except where an update is required to address an active safety or legal issue.

---

*This is a public draft posted for transparency. See the note at the top of the page regarding legal review status.*
