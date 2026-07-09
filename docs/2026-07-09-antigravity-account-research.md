# optio-antigravity `analyze_account` — account-analysis research

**Date:** 2026-07-09
**Status:** research complete + **LIVE CAPTURE DONE** (read-only). `capture_status = captured`.
No source built, no code edited.
**Companion fixtures:** `packages/optio-antigravity/tests/fixtures/account_{userinfo,loadCodeAssist,quotaSummary,fetchAvailableModels}.json`
(PII-scrubbed; captured 2026-07-09 08:2x UTC against the fresh seed `_id
6a4f591848b8fe6c36f9b20d`, a `free-tier` consumer Google account).
**Design:** `docs/2026-07-09-account-analysis-design.md`
**Reference analyzers mirrored:** `optio_claudecode/account.py`, `optio_codex/account.py`,
`optio_cursor/account.py`; companion research `docs/2026-07-09-{grok,codex,cursor}-account-research.md`.

## TL;DR

- **agy authenticates only against Google (consumer OIDC).** The seed token is an
  **opaque Google OAuth2 access token** (`ya29.…`, not a JWT). Identity therefore
  comes from Google's **userinfo** endpoint, NOT from a vendor identity API.
- **Identity (name / email / account_id): captured via GET** —
  `GET https://www.googleapis.com/oauth2/v1/userinfo` returned `200` with the
  stored `ya29` bearer (scopes `userinfo.email` + `userinfo.profile` granted).
  Real v1 shape: `{id, email, verified_email, name, given_name, family_name,
  picture}` — `id` is a 21-digit numeric Google account id.
- **Plan + usage/limit windows: EXPOSED and CAPTURED via POST `v1internal:` methods**
  on Google Cloud Code Assist (`https://cloudcode-pa.googleapis.com`), the same
  backend agy uses for inference. Plan/tier + project id → `:loadCodeAssist`
  (`currentTier.id="free-tier"`, `cloudaicompanionProject`); per-model usage
  windows with reset times → `:retrieveUserQuotaSummary` (and per-model
  `quotaInfo` on `:fetchAvailableModels`). All are POST-only Google
  gRPC-over-HTTP `v1internal:` verbs and READ-ONLY (they only read plan/quota
  state — verified 200s, no quota consumed).
- **The "Individual quota reached. Resets in Nh" signal lives in the quota
  buckets** returned by `:retrieveUserQuotaSummary` (`remainingFraction → 0`,
  `resetTime` = the weekly reset on the exhausted per-model bucket). On the
  fresh free-tier seed captured here every bucket was full (`remainingFraction ==
  1`, `resetTime = now + 7d`), so `pct == 0` for all — but the field path is
  now confirmed against real JSON, so `AccountInfo.windows` + `is_limited(...)`
  are fully populatable.

## 1. Credential layout (from the wrapper)

Source: `optio_antigravity/verify.py`, `seed_manifest.py`.

- **Seed-blob member holding creds:** `.gemini/antigravity-cli/antigravity-oauth-token`
  (`_TOKEN_STORE_RELPATH` in `seed_manifest.py`, imported as `_TOKEN_MEMBER` by
  `verify.py`). The seed tar.gz also carries `.gemini/antigravity-cli/settings.json`,
  `.gemini/antigravity-cli/cache/onboarding.json`, and the `.gemini/config/` tree
  (none credential-bearing).
- **Token-store shape (nested):**
  ```json
  {"auth_method": "consumer",
   "token": {"access_token": "ya29.…", "token_type": "Bearer",
             "refresh_token": "1//0…",
             "expiry": "2026-07-09T05:03:43.061204688+02:00"}}
  ```
  The access/refresh tokens live under `token` (not top-level). `auth_method:
  "consumer"` = a personal Google account (not a Workspace/Cloud project login).
- **Access-token field:** `store["token"]["access_token"]` — this is the bearer to
  pass to `analyze_account`. It is an **opaque Google `ya29.…` token** (258 chars in
  the live seed), so — unlike grok — there are **no identity claims embedded**; every
  identity/plan/usage read is a network call.
- **`expiry`** is Go RFC3339Nano (nanosecond fractional seconds + tz offset); a
  Google access token lives ~1 h. **The analyzer must not refresh** (single-use
  rotating refresh token; a refresh without save-back strands the seed — same rule
  as every other wrapper).
- **Vendor auth used by `verify.py`:** OIDC discovery at
  `https://accounts.google.com/.well-known/openid-configuration` → `token_endpoint`
  (`https://oauth2.googleapis.com/token`, refresh) + `userinfo_endpoint` (liveness).
  Public PKCE client, `client_id` only (no secret):
  `_AGY_CLIENT_ID = 1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com`.
  Bearer header `Authorization: Bearer <access_token>`, UA `optio-antigravity-seed-verify/1`.

## 2. Vendor endpoints

### 2a. Identity — Google userinfo (GET; the analyzer's identity source)

| Purpose | Method | URL | Auth | Returns |
|---|---|---|---|---|
| OIDC discovery | GET | `https://accounts.google.com/.well-known/openid-configuration` | none | endpoints |
| **Identity** | GET | `https://www.googleapis.com/oauth2/v1/userinfo` | `Bearer <ya29>` | `id, email, verified_email, name, given_name, family_name, picture` |
| Identity (OIDC v3) | GET | `https://www.googleapis.com/oauth2/v3/userinfo` (== `userinfo_endpoint`) | `Bearer <ya29>` | `sub, email, email_verified, name, given_name, family_name, picture` |
| Token debug | GET | `https://oauth2.googleapis.com/tokeninfo?access_token=<ya29>` | (token in query) | `aud, scope, email, expires_in` — read-only; returned `invalid_token` here (expired) |

`userinfo` is the read-only GET the analyzer should use for name/email/account_id.
No vendor identity API beyond Google (agy is a pure Google-OIDC consumer login).

### 2b. Plan + usage — Google Cloud Code Assist (POST-only `v1internal:` verbs)

Base: `https://cloudcode-pa.googleapis.com` (agy's inference + quota backend; a
`daily-cloudcode-pa.sandbox.googleapis.com` sandbox mirror also exists).
Headers: `Authorization: Bearer <ya29>`, `Content-Type: application/json`,
`Accept: application/json`. A `User-Agent: antigravity/<ver> <os>/<arch>` was
sent but is not required. **VERIFIED (live capture):** the `metadata`
`ClientMetadata` object accepts ONLY `{"ideType","platform","pluginType"}` here
(e.g. `{"ideType":"ANTIGRAVITY","platform":"PLATFORM_UNSPECIFIED","pluginType":"GEMINI"}`).
The third-party-doc guesses `extensionName`/`locale`/`ideName` are **rejected**
with HTTP 400 `Unknown name "extensionName" … Cannot find field`.

**Verified request bodies (each returned 200, read-only):**
- `:loadCodeAssist` → body `{"metadata": {ideType,platform,pluginType}}`.
- `:retrieveUserQuotaSummary` → body **`{}` (empty)**. A `{"metadata":…}` wrapper
  is **rejected** (HTTP 400 `Unknown name "metadata"`); the token alone scopes the
  caller, so **no project id is needed** for this call (the §-old claim that a
  project must be fetched first is WRONG for quotaSummary).
- `:fetchAvailableModels` → body `{"project":"<PROJECT_ID>"}` (project from
  `loadCodeAssist.cloudaicompanionProject`).

| Purpose | Method | URL | Body (verified) | Key response fields (verified) |
|---|---|---|---|---|
| Project + plan discovery | POST | `…/v1internal:loadCodeAssist` | `{"metadata":{ideType,platform,pluginType}}` | `currentTier.{id,name,…}`, `allowedTiers[]`, `paidTier`, `cloudaicompanionProject`, `gcpManaged`. NO `planInfo`/`availablePromptCredits` on a consumer free tier |
| **Usage windows (per-model)** | POST | `…/v1internal:retrieveUserQuotaSummary` | `{}` | `groups[].{displayName, description, buckets[].{bucketId, displayName, description, resetTime, remainingFraction}}` — `remainingFraction` is **FLAT on the bucket** (not `remaining.remainingFraction`); one group `displayName:"All Models"`, buckets are **per-model** |
| Usage (per-model, finer) | POST | `…/v1internal:fetchAvailableModels` | `{"project":"<PROJECT_ID>"}` | `models{<id>:{displayName, quotaInfo:{resetTime[, remainingFraction, isExhausted]}, …}}` — `models` is a **dict keyed by model id**; `quotaInfo.remainingFraction` omitted when full |
| Inference (429 source) — **NOT CALLED** | POST | `…/v1internal:streamGenerateContent?alt=sse` | Gemini request | emits `Individual quota reached … Resets in Nh` 429 — consumes quota, deliberately not touched |

All `v1internal:` verbs are **POST-only** (Google gRPC-over-HTTP colon-method style).

## 3. Real captured payloads

**CAPTURED.** The fresh seed (`gm_antigravity_seeds`, `_id
6a4f591848b8fe6c36f9b20d`, `blobId 6a4f591848b8fe6c36f9b20b`) held a **valid**
access token (`expiry = 2026-07-09T11:17:11+02:00` = `09:17:11Z`; capture time
~`08:2x Z`, ~57 min of validity). Blob decrypted cleanly via
`engine.credentials.decrypt_session_blob` (age-encrypted, not plain gzip). No
refresh was performed. All four reads returned 200; no `streamGenerateContent`,
no Mongo writes, no source edits.

**`userinfo` (GET oauth2/v1/userinfo)** — scrubbed shape in
`fixtures/account_userinfo.json`:
```json
{"id":"<21-digit numeric>","email":"…","verified_email":true,
 "name":"…","given_name":"…","family_name":"…","picture":"https://…"}
```

**`loadCodeAssist`** — `fixtures/account_loadCodeAssist.json`. Consumer free tier:
- `currentTier = {"id":"free-tier", "name":"Antigravity", "description":…,
  "privacyNotice":…, "upgradeSubscriptionUri":…, "upgradeSubscriptionType":"GOOGLE_ONE"}`
- `allowedTiers = [free-tier "Antigravity", standard-tier "Antigravity"]`
- `paidTier = {"id":"free-tier", "name":"Antigravity Starter Quota", …}`
- `cloudaicompanionProject = "<project id>"` (kebab id, e.g. `restful-lens-…`)
- `gcpManaged = false`. **No** `planInfo`, **no** `availablePromptCredits` keys.
- NB `currentTier.name` is the PRODUCT label (`"Antigravity"`), NOT a plan tier;
  the tier enum is `currentTier.id` (`"free-tier"`). Also `currentTier.upgradeSubscriptionUri`
  embeds the account email as an `Email=` query param (scrubbed in the fixture).

**`retrieveUserQuotaSummary`** (body `{}`) — `fixtures/account_quotaSummary.json`.
`{"groups":[{"displayName":"All Models","description":…,"buckets":[…]}],"description":…}`.
ONE group; 8 per-model buckets, each:
```json
{"bucketId":"gemini-pro-agent","displayName":"Gemini 3.1 Pro (High)",
 "resetTime":"2026-07-16T08:21:36Z","description":"Quota resets in 6 days, 23 hours.",
 "remainingFraction":1}
```
Bucket ids captured: `gemini-3.5-flash-{low,extra-low}`, `gemini-3-flash-agent`,
`gemini-3.1-pro-low`, `gemini-pro-agent`, `claude-sonnet-4-6`,
`claude-opus-4-6-thinking`, `gpt-oss-120b-medium`. All `remainingFraction==1`
(pct 0 — a fresh free-tier account), all reset `2026-07-16T08:21:36Z` (≈ now+7d).

**`fetchAvailableModels`** (body `{"project":…}`) — `fixtures/account_fetchAvailableModels.json`.
`models` is a **dict keyed by model id** (20 models); each value has `displayName,
maxTokens, maxOutputTokens, tokenizerType, quotaInfo, model, apiProvider,
modelProvider, modelExperiments`. Here every `quotaInfo` carries only `resetTime`
(no `remainingFraction` → full). Top-level also lists routing sets
(`defaultAgentModelId`, `agentModelSorts`, `commandModelIds`, `tabModelIds`, …).

## 4. Fixtures

**Written** (PII-scrubbed), under `packages/optio-antigravity/tests/fixtures/`:
- `account_userinfo.json` — the GET userinfo body.
- `account_loadCodeAssist.json` — tier/project payload.
- `account_quotaSummary.json` — per-model quota buckets (primary window source).
- `account_fetchAvailableModels.json` — per-model catalog + `quotaInfo` (fallback).

Scrubbed to placeholders: `id → 000000000000000000000`, `email → user@example.com`
(incl. the url-encoded `Email=` param), `name → Test User`, `given/family → Test/User`,
`picture → https://example.com/photo.jpg`, `cloudaicompanionProject →
example-project-0000`. Kept **verbatim**: `currentTier.id`/`name`, every bucket's
`bucketId`/`displayName`/`remainingFraction`/`resetTime`/`description`, and all model
ids. A grep for the real email/name/project over the on-disk fixtures is clean.

## 5. AccountInfo field mapping proposal

`analyze_account(access_token: str)` — the `ya29` bearer from
`store["token"]["access_token"]`. Fail-soft → `EMPTY` on any error (mirrors
claudecode). Identity is one GET; plan+usage are POSTs (see the caveat below).

**VALIDATED against the real payload** (fixtures in §4).

| AccountInfo field | Source (JSON path) | Notes (validated) |
|---|---|---|
| `name` | `userinfo.name` | `given_name`+`family_name` also present |
| `email` | `userinfo.email` | `verified_email:true` on this seed |
| `account_id` | `userinfo.id` (v1) | 21-digit numeric Google account id. (`v3` would use `sub`.) NOT the CCA project id |
| `plan` | prettified `loadCodeAssist.currentTier.id` | Real value `"free-tier"`. Use `currentTier.id` (the tier enum), NOT `currentTier.name` (which is the product label `"Antigravity"`). Prettify: strip `-tier`/`_`, title-case → `"Free"`. No `planInfo` key exists on consumer tiers |
| `windows` | `retrieveUserQuotaSummary.groups[].buckets[]` → one `UsageWindow` per bucket | per-window mapping below |
| `raw` | `{"userinfo":…, "loadCodeAssist":…, "quotaSummary":…}` | escape hatch |

**Per usage window (from `retrieveUserQuotaSummary`) — VALIDATED:**
- `label` = `bucket.displayName` (e.g. `"Gemini 3.1 Pro (High)"`,
  `"Claude Opus 4.6 (Thinking)"`). There is a single group `displayName:"All Models"`,
  so a `group:bucket` prefix adds no information — use the bucket displayName.
- `pct` = `(1 - bucket.remainingFraction) * 100`. **`remainingFraction` is FLAT on the
  bucket** (real payload), NOT `bucket.remaining.remainingFraction` (a third-party-doc
  guess that was wrong). Guard: treat a missing `remainingFraction` as `1.0` (0% used) —
  the API omits it when a bucket is fully restored.
- `resets_at` = `parse(bucket.resetTime)` (ISO-8601 `…Z`, e.g. `2026-07-16T08:21:36Z`).
- `model` = `bucket.bucketId` (per-model scope, e.g. `"gemini-pro-agent"`,
  `"claude-opus-4-6-thinking"`). This is **per-model**, not group-scoped — the doc's
  earlier "`model = group.displayName`" guess was wrong (there is one catch-all group).

**Alternative per-model windows (from `fetchAvailableModels`):** `models` is a **dict
keyed by model id**; per entry `label = value.displayName`,
`pct = (1 - quotaInfo.remainingFraction)*100` (or `100` if `quotaInfo.isExhausted`;
`0` if `remainingFraction` absent), `resets_at = parse(quotaInfo.resetTime)`,
`model = <dict key>`. `retrieveUserQuotaSummary` is preferred (it is Antigravity's own
"Model Quota" list); `fetchAvailableModels` is the finer-grained fallback and also
carries the model catalog.

The "Individual quota reached. Resets in Nh" maps to the bucket with
`remainingFraction == 0` ⇒ `pct = 100`, so `is_limited(info, now, models=[<bucketId>])`
fires for that model. On the fresh free-tier seed captured here all buckets were full
(`remainingFraction == 1` ⇒ `pct == 0`), so nothing was limited — the exhausted-state
mapping is inferred from the field semantics, not observed on this seed.

## 6. Usage / limits — exposed?

**Yes — CONFIRMED available (captured live).** Unlike grok (no usable usage source),
Antigravity surfaces per-model usage windows with reset times via
`cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary` (per-model buckets
with flat `remainingFraction` + `resetTime`), plus `:fetchAvailableModels` per-model
`quotaInfo` as a finer fallback. Both are POST `v1internal:` verbs but READ-ONLY (200s,
no quota consumed). `retrieveUserQuotaSummary` needs **no project id** (empty `{}` body —
the token scopes the caller); `fetchAvailableModels` needs the project from
`loadCodeAssist`. So `AccountInfo.windows` + `is_limited(...)` are fully populatable for
antigravity — a capability grok lacks. Identity (name/email/id) is a plain GET.

## 7. Blockers / open items

1. **RESOLVED — live capture done.** The fresh seed's `ya29` token was valid; all
   four reads returned 200. `capture_status = captured`. No refresh performed.
2. **Only 1 seed present** in `gm_antigravity_seeds` (brief mentioned a fresh one
   replacing the expired one). The one present is the fresh, valid seed.
3. **RESOLVED — request shapes locked** against real payloads (§2b): metadata accepts
   only `{ideType,platform,pluginType}`; `retrieveUserQuotaSummary` takes an empty `{}`
   body (no project needed); `fetchAvailableModels` takes `{"project":…}`. `currentTier.id
   = "free-tier"`; no `planInfo` key on consumer tiers.
4. **Identity-only fast path remains a safe incremental option** — `analyze_account`
   can ship name/email/account_id from `userinfo` (one GET) with `windows=()`, then add
   the two read-POSTs for plan+windows.
5. **RESOLVED — field names locked.** `remainingFraction` is **flat on the bucket**
   (not nested `remaining.remainingFraction`); `bucket.displayName` is the per-model
   label; `resetTime` is per-bucket. One catch-all group `"All Models"`; buckets are
   per-model keyed by `bucketId`.
6. **Exhausted-state (pct=100) not observed.** The captured seed is fresh (all
   `remainingFraction==1`). The `remainingFraction==0 ⇒ limited` mapping is inferred
   from field semantics; a re-capture on an exhausted account would confirm the exact
   omit-vs-zero behavior and any `isExhausted` flag.

## 8. Environment / method (reproducibility)

- Mongo `mongodb://localhost:27017`, db `excavator`, coll `gm_antigravity_seeds`
  (1 seed; `_id 6a4f591848b8fe6c36f9b20d`, `blobId 6a4f591848b8fe6c36f9b20b`). Blob
  decrypted via `engine.credentials.decrypt_session_blob` (age-encrypted here), then
  the tar.gz member `.gemini/antigravity-cli/antigravity-oauth-token` parsed for the
  nested token store → `store["token"]["access_token"]`.
- Python `/home/csillag/deai/excavator/.venv/bin/python`,
  `EXCAVATOR_KEY_DIR=/home/csillag/excavator-trinkets`.
- Read-only calls issued (all 200): `GET oauth2/v1/userinfo`; POST
  `v1internal:loadCodeAssist` `{"metadata":{ideType,platform,pluginType}}`; POST
  `v1internal:retrieveUserQuotaSummary` `{}`; POST `v1internal:fetchAvailableModels`
  `{"project":…}`. NO `streamGenerateContent`/generate. No Mongo writes, no token
  refresh, no source edits, no commits.

### Sources
- Backend + endpoints: <https://docs.picoclaw.io/docs/providers/antigravity/>,
  <https://github.com/NoeFabris/opencode-antigravity-auth/blob/main/docs/ANTIGRAVITY_API_SPEC.md>,
  <https://github.com/steipete/CodexBar/blob/main/docs/antigravity.md>
- Quota shape (`retrieveUserQuota`/`Summary`, groups/buckets/`remainingFraction`/`resetTime`):
  CodexBar docs + <https://github.com/steipete/CodexBar/issues/577>,
  <https://gist.github.com/taoalpha/22773d2132519e55a4c7427fd3e96d8e>
- "Individual quota reached / Resets in Nh":
  <https://github.com/google-antigravity/antigravity-cli/issues/163>,
  <https://github.com/can1357/oh-my-pi/issues/2198>,
  <https://github.com/google-gemini/gemini-cli/discussions/27307>
- Cloud Code Assist provider parity: <https://github.com/NousResearch/hermes-agent/issues/49099>
</content>
</invoke>
