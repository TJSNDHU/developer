# PCI-DSS / Payment Security Skill

Apply this skill whenever the task touches Stripe, PayPal, Razorpay, card
data, checkout flows, payment webhooks, billing, invoices, refunds, or
subscription management.

## Hard rules (PCI-DSS v4.0)

1. **NEVER** log full PANs (Primary Account Numbers), CVV, CVC2, track
   data, or PIN blocks. Even truncated card numbers must be masked to at
   most the first 6 + last 4 digits (`411111******1111`).
2. **NEVER** store CVV / CVC2 / CVC after authorization — even encrypted.
   It is a PCI violation under Req 3.2.
3. **NEVER** transmit card data over unencrypted channels. All endpoints
   that touch card data MUST be HTTPS / TLS 1.2+.
4. **NEVER** accept card numbers directly on your server. Use the
   provider's hosted fields / Elements (Stripe.js, PayPal SDK, etc.) so
   card data never hits your origin → reduces PCI scope from SAQ-D to
   SAQ-A.
5. **NEVER** trust client-supplied amounts. Define price packages
   server-side (`PACKAGES = {"pro": Decimal("29.00"), ...}`) and look up
   the amount by package key only.

## Stripe / Razorpay / PayPal patterns

### Server-defined packages (anti-tampering)

```python
# routers/payments.py
PACKAGES: dict[str, Decimal] = {
    "pro":  Decimal("29.00"),
    "team": Decimal("99.00"),
}

@router.post("/checkout")
async def checkout(body: CheckoutBody, user=Depends(current_user)):
    if body.tier not in PACKAGES:
        raise HTTPException(400, "Unknown tier")
    amount = PACKAGES[body.tier]
    # NEVER trust amount/price from the client
```

### Webhook signature verification (mandatory)

```python
import stripe
@router.post("/webhook/stripe")
async def webhook(request: Request,
                  stripe_signature: str = Header(None)):
    body = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            payload=body,
            sig_header=stripe_signature,
            secret=os.environ["STRIPE_WEBHOOK_SECRET"],
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(400, "Invalid signature")
```

A webhook handler that does NOT verify the signature is fraud-bait —
attackers POST fake `checkout.session.completed` events to flip users
to paid tiers.

### Idempotency (mandatory for payment side-effects)

```python
# Before flipping tier or granting tokens, claim an idempotency lock so
# duplicate webhook deliveries (Stripe retries on 5xx) don't double-credit.
result = await db.cto_payments.update_one(
    {"session_id": session_id, "status": {"$ne": "paid"}},
    {"$set": {"status": "paid", "paid_at": now}},
)
if result.modified_count == 0:
    return {"ok": True, "already_processed": True}
```

## Forbidden code patterns

| ❌ Anti-pattern                                           | ✅ Replace with                                                |
|----------------------------------------------------------|---------------------------------------------------------------|
| `logger.info(f"card={req.card_number}")`                 | Mask: `f"card=****{req.card_number[-4:]}"`                    |
| `db.users.update_one({...}, {"$set": {"cvv": cvv}})`     | Don't store CVV. Period.                                       |
| Reading `amount` from the request body                   | Look up amount by `package_id` server-side                    |
| Skipping `stripe.Webhook.construct_event(...)`           | Always verify signature with `STRIPE_WEBHOOK_SECRET`          |
| `if event['type'] == 'paid': grant(token)` (no idempotency) | Conditional `update_one(filter w/ status != paid)` first |
| Returning the full Stripe `customer_id` to the client    | Use your own opaque IDs in the UI                              |

## Secrets

- `STRIPE_API_KEY` (server-side only, NEVER ship to frontend).
- `STRIPE_PUBLISHABLE_KEY` is OK on the frontend (Stripe.js).
- `STRIPE_WEBHOOK_SECRET` (server-side only).
- Rotate immediately if a secret leaks: Stripe dashboard → Developers →
  API keys → Roll. Update env, restart.

## Logging policy

Logging a payment event is fine — logging the contents is not:
- ✅ `logger.info("checkout completed", extra={"session_id": sid, "tier": tier})`
- ❌ `logger.info(f"raw stripe payload: {event}")` — the payload may
  contain billing_details with PII / card metadata.

## Compliance checklist before shipping a payment feature

1. Card data never touches our origin (hosted fields / Elements only)
2. All amounts are server-defined
3. Webhook signature verified
4. Side-effects are idempotent
5. No PAN / CVV in logs, DB, or analytics
6. HTTPS enforced for every payment route
7. Keys live in env vars, NOT in the repo
