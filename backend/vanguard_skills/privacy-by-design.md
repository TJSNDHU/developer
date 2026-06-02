# Privacy-by-Design Skill (GDPR · CCPA · DPDP)

Apply this skill whenever the task touches user data, personal info,
PII, profiles, analytics, telemetry, cookies, GDPR / CCPA / DPDP / LGPD
consent, data export, data deletion, account closure, or any "right to
be forgotten" flow.

## Core principles (GDPR Art. 25)

1. **Data minimisation** — collect only fields you NEED. If unsure
   whether you'll use it, don't ask for it. Less data = less liability.
2. **Purpose limitation** — every field stored must map to a documented
   purpose. No "we might want it later" stockpiling.
3. **Storage limitation** — set a TTL on every PII collection. Expired
   data is auto-deleted, not "soft-deleted forever".
4. **Integrity & confidentiality** — encrypt PII at rest. Encrypt
   credentials in transit. Never log raw PII.
5. **Accountability** — every PII read/write must be auditable: who
   touched what, when, why.

## PII categories (treat each differently)

| Category               | Examples                                          | Min protection                       |
|-----------------------|---------------------------------------------------|---------------------------------------|
| Identifiers           | email, phone, IP, user_id                         | Hashed for analytics, plaintext OK in core DB |
| Sensitive             | name, DOB, address, gender                        | Encrypted at rest, masked in logs    |
| Financial             | bank a/c, card last4, transactions                | See `pci-compliance.md`              |
| Health / Biometric    | medical records, fingerprints                     | Encrypted, separate DB, signed audit |
| Special (GDPR Art. 9) | race, religion, sexual orientation, union status  | Don't collect unless legally required |

## Mandatory user rights (GDPR Art. 15-22)

Every PII-touching app MUST expose these endpoints:

```python
# 1. Right to access (Art. 15)
@router.get("/me/data-export")
async def data_export(user=Depends(current_user)):
    return {
        "profile": user.dict(exclude={"password_hash"}),
        "sessions": await db.chat_sessions.find({"user_id": user.id}).to_list(None),
        "tasks":    await db.cto_tasks.find({"user_id": user.id}).to_list(None),
        # ... every collection that stores their data
    }

# 2. Right to rectification (Art. 16)
@router.patch("/me")
async def update_me(body: UpdateBody, user=Depends(current_user)):
    ...

# 3. Right to erasure / "be forgotten" (Art. 17)
@router.delete("/me")
async def delete_me(user=Depends(current_user)):
    # Hard-delete or hard-anonymise across ALL collections.
    for coll in ("dev_users", "chat_sessions", "cto_tasks",
                 "cto_projects", "cto_payments", "ora_council_logs"):
        await db[coll].delete_many({"user_id": user.id})
    return {"ok": True}

# 4. Right to portability (Art. 20) — JSON / CSV machine-readable export
# 5. Right to object (Art. 21) — opt-out of marketing, analytics
```

## Forbidden patterns

| ❌ Anti-pattern                                                  | ✅ Replace with                                              |
|------------------------------------------------------------------|-------------------------------------------------------------|
| `logger.info(f"user signed up: {email} {phone} {dob}")`          | `logger.info("user signup", extra={"user_id": uid})`        |
| Setting analytics cookies before consent                         | Cookie banner first, default = essential-only                |
| `db.users.find_one({"email": email})` over an unindexed field   | Index sensitive fields + rate-limit lookups (prevents email enumeration) |
| Sending full PII payloads to Sentry / DataDog                    | Configure `before_send` to strip emails/IPs/names           |
| "Soft delete" a user but keep their chats, tasks, audit logs forever | Hard-anonymise on delete (replace user_id with `deleted_<hash>`) |
| Storing IP in plaintext for years                                | Hash IP for analytics, or truncate last octet (`/24`)        |

## Consent UX (the legal bit)

Consent must be:
- **Freely given** — no pre-ticked boxes. No "Reject" hidden 3 clicks deep.
- **Specific** — per-purpose. One toggle per: "analytics", "marketing",
  "personalisation", "third-party sharing". No "Accept all" without an
  equally prominent "Reject all".
- **Informed** — link to a plain-language privacy policy.
- **Withdrawable** — settings page must let the user revoke as easily as
  they consented.

Store the consent decision with timestamp + version of the policy text
they agreed to (for audit): `{user_id, kind: "analytics", granted: bool,
ts, policy_version}`.

## Encryption at rest (PII)

For any field marked sensitive (DOB, address, ID numbers, health, etc.):

```python
# Use the same HKDF-Fernet pattern as the PAT vault.
from services.vault import encrypt_field, decrypt_field
doc = {
    "user_id": uid,
    "email": email,           # OK plaintext (needed for login lookup)
    "address_enc": encrypt_field(address, scope="user_addr", subject=uid),
}
```

Logs must NEVER contain decrypted PII. Treat encrypted blobs the same as
ciphertext — don't print them either, they're indexable by an attacker.

## Data Retention Policy template

Every collection that stores PII gets a documented retention rule:

| Collection         | Retention                | Trigger                    |
|--------------------|---------------------------|----------------------------|
| `chat_sessions`    | 90 days from last update  | TTL index on `updated_at`  |
| `support_tickets`  | 2 years from `resolved_at`| Background cron            |
| `audit_logs`       | 7 years (legal hold)      | Cold storage after 90 days |
| `marketing_opt_in` | until user revokes        | Explicit user action       |

## Compliance checklist before shipping a PII feature

1. Every field has a documented purpose
2. Sensitive fields are encrypted at rest
3. Logs are PII-free (use IDs, never emails / names / IPs)
4. `GET /me/data-export` returns every row touching this user
5. `DELETE /me` purges every row touching this user
6. Consent banner present where applicable
7. Retention TTL set for new collections
8. Cross-border transfer noted in the privacy policy
   (where is the DB? where is Sentry? where is the LLM provider?)
