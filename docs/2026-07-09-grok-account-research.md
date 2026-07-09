# optio-grok `analyze_account` — account-analysis research

**Date:** 2026-07-09
**Status:** research complete (read-only). No source built, no code edited.
**Companion fixture:** `packages/optio-grok/tests/fixtures/account_capture.json`
**Design:** `docs/2026-07-09-account-analysis-design.md`

## TL;DR

- **Identity + plan: fully capturable via GET, verified against a live seed.**
  - Identity (name/email/account_id) → OIDC `userinfo` and/or `api.x.ai/v1/me`.
  - Plan → `grok.com/rest/subscriptions` (`tier` + `status` of the active row).
- **Usage / rate-limit windows: NOT exposed via any GET endpoint reachable with
  the CLI's OAuth token.** The only candidate (`grok.com/rest/rate-limits`) is
  POST-only (returns HTTP 501 "Method Not Allowed" on GET) and its acceptance of
  this OAuth bearer is unverified. So grok's `AccountInfo.windows` will be empty
  (`is_limited` → always False) unless a follow-up POST probe is authorized.

## 1. Credential layout (from the wrapper)

Source: `optio_grok/verify.py`, `seed_manifest.py`, `cred_watcher.py`.

- **Seed-blob member holding creds:** `.grok/auth.json` (`_AUTH_MEMBER` in
  `verify.py`; `GROK_CRED_MANIFEST.include`). The seed tar.gz also carries
  `.grok/config.toml` (not credential-bearing).
- **auth.json shape:** a single-key dict `{ "<issuer>::<client_id>": { ...creds... } }`.
  The top key here is `https://auth.x.ai::b1a00492-073a-47ea-816f-4c329264a828`.
- **Access-token field:** `creds["key"]` — an ES256 JWT (`iss=https://auth.x.ai`).
  This is the bearer to pass to `analyze_account`. `creds` also directly carries
  identity already: `email`, `first_name`, `user_id`, `team_id`, `principal_*`,
  plus `refresh_token`, `expires_at`, `oidc_issuer`, `oidc_client_id`.
- **JWT claims** (decoded `creds["key"]`): `iss, sub, aud, exp, iat, scope,
  principal_type, principal_id, client_id, jti, tier, team_id`. `scope =
  "openid profile email offline_access grok-cli:access api:access
  conversations:read conversations:write"`. `tier = 1` (an integer API-quota
  tier, NOT the subscription plan name — see §4).
- **Vendor auth used by verify:** OIDC discovery at
  `https://auth.x.ai/.well-known/openid-configuration`, then `token_endpoint`
  (refresh) and `userinfo_endpoint` (liveness). Public client (`client_id` only,
  no secret). Bearer header `Authorization: Bearer <key>`, UA
  `optio-grok-seed-verify/1`. **The analyzer must not refresh** (single-use
  rotating refresh token; a refresh without save-back strands the seed).

## 2. Vendor endpoints (all verified live, read-only GET, HTTP 200 unless noted)

Auth for all: `Authorization: Bearer <creds.key>`, `Accept: application/json`.

| Purpose | Method | URL | Result |
|---|---|---|---|
| OIDC discovery | GET | `https://auth.x.ai/.well-known/openid-configuration` | 200 |
| **Identity (OIDC)** | GET | `https://auth.x.ai/oauth2/userinfo` | **200** — `sub, name, given_name, family_name, email, email_verified, picture` |
| **Identity (xAI)** | GET | `https://api.x.ai/v1/me` | **200** — `user_id, team_id, zdr_status, team_blocked, oauth.client_id` |
| **Plan / subscription** | GET | `https://grok.com/rest/subscriptions` | **200** — `subscriptions[].{tier,status,billing*,stripe.*}` |
| Model list | GET | `https://api.x.ai/v1/models` | 200 (not needed for account) |
| Model list (CLI proxy) | GET | `https://cli-chat-proxy.grok.com/v1/models` | 200 (not needed) |
| Usage / rate-limits | GET | `https://grok.com/rest/rate-limits` | **501 "Method Not Allowed"** (POST-only; not captured) |
| Usage (other guesses) | GET | `api.x.ai/v1/{usage,rate-limits}`, `grok.com/rest/{usage,user,users/self}`, `cli-chat-proxy…/v1/{me,usage,rate-limits}` | 404 — do not exist |

OIDC discovery advertises scopes `grok-cli:access, team:read, org:read,
conversations:*, workspaces:*, api:access` and claims `sub, name, given_name,
family_name, email, email_verified, picture`.

Note: the CLI's OAuth bearer is accepted by `api.x.ai` for GET `/v1/me` and
`/v1/models` (it is NOT a developer `xai-…` API key — `/v1/api-key` returns
`API key is missing`). GET responses on `api.x.ai/v1/me` carry **no**
`x-ratelimit-*` headers (those appear only on paid-API inference POSTs and
reflect the developer-API tier, not the coding subscription).

## 3. Real captured payloads (scrubbed copies in the fixture)

`GET /oauth2/userinfo`:
```json
{"sub":"<uuid>","name":"<name>","given_name":"<name>","family_name":"",
 "picture":"users/<uuid>/<asset>-profile-picture.webp",
 "email":"<email>","email_verified":true}
```

`GET /v1/me`:
```json
{"user_id":"<uuid>","team_id":"<uuid>","zdr_status":"no_zdr",
 "team_blocked":false,"oauth":{"client_id":"<uuid>"}}
```

`GET /rest/subscriptions` — an array of historical Stripe rows; the **current**
one is the entry with `"status":"SUBSCRIPTION_STATUS_ACTIVE"` (also the max
`billingPeriodEnd`):
```json
{"subscriptions":[ ...,
  {"stripe":{"subscriptionId":"sub_…","productId":"prod_…",
             "currentPeriodEnd":"2026-08-08T02:32:37Z","subscriptionType":"MONTHLY"},
   "xaiUserId":"<uuid>","tier":"SUBSCRIPTION_TIER_GROK_PRO",
   "status":"SUBSCRIPTION_STATUS_ACTIVE",
   "billingInterval":"BILLING_INTERVAL_MONTHLY",
   "billingPeriodEnd":"2026-08-08T02:32:37Z","cancelAtPeriodEnd":false}]}
```

## 4. AccountInfo field mapping proposal

`analyze_account(creds)` where `creds = auth["<iss>::<client>"]` (the dict), or
minimally its `creds["key"]` bearer. Two network GETs (identity + plan); both
fail-soft. Identity can also be read straight off `creds` with zero network.

| AccountInfo field | Source (JSON path) | Notes |
|---|---|---|
| `name` | `userinfo.name` → else `creds.first_name` | `given_name`+`family_name` also available; `family_name` often empty |
| `email` | `userinfo.email` → else `creds.email` | verified via `email_verified` |
| `account_id` | `userinfo.sub` (== `v1_me.user_id` == `creds.user_id`) | vendor user uuid |
| `plan` | prettified `subscriptions[active].tier` | `SUBSCRIPTION_TIER_GROK_PRO` → `"Grok Pro"`; strip `SUBSCRIPTION_TIER_` prefix, title-case tokens. Pick the row with `status == SUBSCRIPTION_STATUS_ACTIVE`; if none active → the subscription is lapsed (plan `None` or the latest tier flagged inactive) |
| `windows` | **empty tuple** | no GET usage source (see §5) |
| `raw` | `{"userinfo":…, "v1_me":…, "subscriptions":…}` | escape hatch |

Zero-network fallback: `creds.{email,first_name,user_id}` already give
name/email/account_id without any HTTP call; the JWT `tier` claim (int) is the
API quota tier, not the plan — do NOT use it for `plan` (use `/rest/subscriptions`).

Plan-picker rule (mirrors claudecode's tier prettifier): choose the
`subscriptions[]` entry whose `status == "SUBSCRIPTION_STATUS_ACTIVE"` (there is
exactly one in the live data); prettify its `tier`. Product-id `prod_…` maps to
the same plan but is opaque — prefer the `tier` enum.

## 5. Usage / limits — exposed?

**No usable GET source.** Findings:

- `grok.com/rest/rate-limits` exists but is **POST-only** (GET → 501 code 12
  UNIMPLEMENTED, the grpc-gateway "wrong method" signal — same as `/rest/models`
  which is a known POST endpoint). The grok.com web app queries it as a POST
  with a body enumerating model kinds; the response carries per-window remaining
  counts + reset windows. **Not captured** here (GET-only research rule; also
  unverified whether it accepts this OAuth bearer vs a web session cookie).
- `api.x.ai` GET responses carry no `x-ratelimit-*` headers; those exist only on
  paid developer-API inference calls and describe the API tier, not the
  SuperGrok/coding subscription the CLI uses.
- The consumer SuperGrok limits (e.g. "N queries / 2h") are documented only as
  human-facing figures, with no per-account usage/reset API surfaced to the CLI
  token.

**Consequence for the analyzer:** `windows = ()`, so `is_limited(...)` is always
False for grok — parity with the other bool-only wrappers on the usage gate,
while still delivering identity + plan. `supports_usage_gating` stays False for
grok; `supports_accounts` can flip True.

## 6. Blockers / open items

1. **Usage windows unavailable read-only.** Populating `windows`/`is_limited`
   for grok requires a **POST** to `grok.com/rest/rate-limits` (with a model-kind
   body) — out of scope for this GET-only research and needs explicit approval to
   probe. Until then grok ships identity+plan only. Verifying that endpoint
   accepts the CLI OAuth bearer (vs a browser session cookie) is the first step
   of any follow-up.
2. **Plan naming** relies on the `SUBSCRIPTION_TIER_*` enum; only
   `SUBSCRIPTION_TIER_GROK_PRO` observed. Other tiers (Heavy / free / team) need
   real captures to lock the prettifier — but the prefix-strip + title-case rule
   generalizes.
3. **No token refresh performed** (rotating single-use refresh token; refresh
   without a real save-back would kill the seed — per task rule). All captures
   used the stored, still-valid access token (~4 h to expiry at capture time).

## 7. Environment / method (reproducibility)

- Mongo `mongodb://localhost:27017`, db `excavator`, coll `gm_grok_seeds`
  (3 live seeds; used `_id=6a4ee77dd294d799cff2bfd1`).
- Seed GridFS blob (`doc["blobId"]`) was **plain gzip** here (not age-encrypted);
  `engine.credentials.decrypt_session_blob` is a no-op / errors on it — extract
  the tar.gz directly. (The wrapper passes an injected `decrypt`; in this DB it
  is identity.)
- All vendor calls: read-only `GET`, `Authorization: Bearer <creds.key>`. No
  Mongo writes, no refresh/rotation, no source edits, no commits.
</content>
