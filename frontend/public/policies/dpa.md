# Data Processing Agreement (DPA) — ORA by Aurem CTO
**Version 1.0 — Last updated: February 12, 2026**

This Data Processing Agreement ("DPA") is entered into between the **Customer** (as defined in the Order Form or account signup) and **Polaris Built Inc**, a corporation incorporated in Canada ("Aurem CTO", "we", "us"), and forms part of the [Terms of Service](/terms) or executed Order Form ("Agreement").

By subscribing to a Team or Enterprise plan, the Customer accepts this DPA. Enterprise customers may execute a countersigned copy — email **privacy@auremcto.com** to request one.

---

## 1. Definitions

Terms used but not defined here have the meaning in the Agreement or in **Regulation (EU) 2016/679 (GDPR)**, the **UK Data Protection Act 2018**, the **California Consumer Privacy Act (CCPA/CPRA)**, the **Digital Personal Data Protection Act, 2023 (DPDP, India)**, and the **Personal Information Protection and Electronic Documents Act (PIPEDA, Canada)**.

- **"Customer Personal Data"** — personal data that Customer or Customer's authorised users submit to the Service.
- **"Controller"** — Customer (or Customer's own customer, where Customer acts as processor).
- **"Processor"** — Polaris Built Inc, processing Customer Personal Data on behalf of the Controller.
- **"Subprocessor"** — a third-party engaged by Processor to process Customer Personal Data (see [Subprocessor List](/subprocessors)).

---

## 2. Roles and Scope

2.1 Customer is the **Controller** and Aurem CTO is the **Processor** in respect of Customer Personal Data submitted to the Service.

2.2 The subject-matter, nature, purpose, duration, categories of data, and categories of data subjects are described in **Annex I**.

2.3 Aurem CTO processes Customer Personal Data only:
- (a) to provide the Service in accordance with the Agreement,
- (b) on Customer's documented instructions (including via account configuration), and
- (c) as required by applicable law (in which case Aurem CTO will notify Customer unless prohibited).

---

## 3. Processor Obligations

3.1 **Confidentiality.** Personnel authorised to process Customer Personal Data are under written confidentiality obligations.

3.2 **Security.** Aurem CTO maintains the technical and organisational measures described in **Annex II** and in our [Security page](/security).

3.3 **Assistance.** Aurem CTO will assist Customer in fulfilling obligations under GDPR Arts. 32–36 (security, breach notification, DPIAs, prior consultation), CCPA §1798.100 et seq. (consumer rights), DPDP §11 (data-principal rights), and PIPEDA Schedule 1 principles, at Customer's cost where the request exceeds Aurem CTO's standard tooling.

3.4 **Data Subject Requests.** If Aurem CTO receives a data-subject request directly, we will forward it to Customer within **7 days** and not respond substantively (unless required by law).

3.5 **Breach Notification.** Aurem CTO will notify Customer **without undue delay and in any case within 72 hours** of becoming aware of a personal-data breach affecting Customer Personal Data. Notification will include the information required under GDPR Art. 33(3).

---

## 4. Subprocessors

4.1 Customer provides **general authorisation** for Aurem CTO to engage the Subprocessors listed at [/subprocessors](/subprocessors).

4.2 Aurem CTO will:
- (a) enter into a written agreement with each Subprocessor imposing data-protection obligations no less protective than this DPA,
- (b) remain liable for Subprocessor performance,
- (c) provide **at least 30 days' prior notice** by email before adding or replacing a Subprocessor.

4.3 Customer may object to a new Subprocessor within 30 days on reasonable data-protection grounds. If the objection cannot be resolved, Customer may terminate the affected portion of the Service and receive a prorated refund of prepaid fees.

---

## 5. International Data Transfers

5.1 Where transfers of Customer Personal Data outside the EEA / UK / Switzerland require a lawful basis, the parties agree to the **EU Standard Contractual Clauses (SCCs)** (Module 2: Controller → Processor) as adopted by Commission Implementing Decision (EU) 2021/914, incorporated by reference. The UK International Data Transfer Addendum (2022) and the Swiss FDPIC amendments apply where relevant.

5.2 Where Aurem CTO processes personal data of Indian residents outside India, transfers rely on DPDP §16 permitted transfers (subject to any Central Government notification restrictions).

5.3 Cross-border transfers involving PIPEDA-covered data are supported by contractual safeguards imposed on Subprocessors per Section 4.2(a).

---

## 6. Records, Audit & Compliance Assistance

6.1 Aurem CTO maintains records of processing per GDPR Art. 30(2) and makes them available on written request.

6.2 **Audit rights.** Customer may audit Aurem CTO's compliance with this DPA no more than **once per 12-month period** (unless a material breach has occurred), with **30 days' written notice**, during business hours, subject to reasonable confidentiality controls. Audits may be conducted through:
- (a) responses to Customer's security questionnaire, and/or
- (b) review of Aurem CTO's most recent penetration-test summary and SOC 2 report (once available).

Physical/on-site audits are reserved for enterprise customers with a signed Order Form.

---

## 7. Deletion & Return

7.1 Upon termination of the Agreement, Customer may export data via the Service for **30 days**.

7.2 After 30 days, Aurem CTO will delete Customer Personal Data from active systems within **90 days**, and from backups within **180 days**, unless retention is required by applicable law.

7.3 Certificate of deletion available on request at **privacy@auremcto.com**.

---

## 8. Liability

Each party's liability under this DPA is subject to the limitations and exclusions set out in the Agreement.

---

## 9. Governing Law

This DPA is governed by the laws of the **Province of Ontario, Canada**, and the federal laws of Canada applicable therein, without regard to conflict-of-laws principles. Nothing in this Section restricts data-subject rights under their local law.

---

## Annex I — Details of Processing

- **Subject-matter:** Provision of AI-assisted code generation, review, and commit services via the ORA platform.
- **Duration:** For the term of the Agreement plus deletion window in §7.
- **Nature & Purpose:** Automated processing of source-code files and metadata to produce code changes; user-account management; billing; support.
- **Categories of Data Subjects:** Customer's authorised users; Customer's own end users whose personal data may be present in code or commit messages.
- **Categories of Personal Data:** Names, email addresses, GitHub identifiers, IP addresses, source-code contents (which may incidentally contain personal data), billing details.
- **Sensitive Data:** None intended. If secrets are detected pre-flight, they are redacted before LLM processing.

## Annex II — Technical & Organisational Measures

See [/security](/security) for the current version. Highlights:
- TLS 1.2+ in transit; AES-256 at rest for MongoDB Atlas; HKDF-Fernet for GitHub PATs.
- Principle of least privilege on all subprocessor tokens.
- Access logging; alerting on anomalous authentication events.
- Annual third-party penetration test (upon reaching Enterprise scale).
- 30-day secrets-rotation policy for high-privilege credentials.

---

**Contact for DPA execution or amendments:** **privacy@auremcto.com**

**Polaris Built Inc** — Incorporated in Canada
