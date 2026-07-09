# optio-antigravity `analyze_account` — account-analysis research

**Date:** 2026-07-09
**Status:** research complete (read-only). No source built, no code edited.
**Companion fixture:** none — live capture skipped (stored token expired; see §3/§6).
**Design:** `docs/2026-07-09-account-analysis-design.md`
**Reference analyzers mirrored:** `optio_claudecode/account.py`, `optio_codex/account.py`,
`optio_cursor/account.py`; companion research `docs/2026-07-09-{grok,codex,cursor}-account-research.md`.

## TL;DR

- **agy authenticates only against Google (consumer OIDC).** The seed token is an
  **opaque Google OAuth2 access token** (`ya29.…`, not a JWT). Identity therefore
  comes from Google's **userinfo** endpoint, NOT from a vendor identity API.
- **Identity (name / email / account_id): capturable via GET** —
  `GET https://www.googleapis.com/oauth2/v1/userinfo` (or `…/oauth2/v3/userinfo`).
  Reachable read-only with the stored `ya29` bearer (scopes `userinfo.email` +
  `userinfo.profile` are granted). NOT captured here because the only live seed's
  token was **expired** at research time (do-not-refresh rule).
- **Plan + usage/limit windows: EXPOSED, but only via POST `v1internal:` methods**
  on Google Cloud Code Assist (`https://cloudcode-pa.googleapis.com`), the same
  backend agy uses for inference. Plan/tier → `:loadCodeAssist`; per-model-group
  usage windows with reset times → `:retrieveUserQuotaSummary` (and per-model
  `quotaInfo` on `:fetchAvailableModels`). These are POST-only (Google
  gRPC-over-HTTP `v1internal:` verbs) and require a project id from `loadCodeAssist`
  first — out of scope for GET-only research (parity with grok's POST-only
  `rest/rate-limits`), and unreachable here anyway with an expired token.
- **The "Individual quota reached. Resets in 121h" signal lives in the quota
  buckets** returned by `:retrieveUserQuotaSummary` / `:fetchAvailableModels`
  (`remainingFraction == 0`, `resetTime ≈ now + ~121h` = the weekly window on the
  exhausted model group). So `AccountInfo.windows` IS populatable — but only if a
  follow-up POST probe against Cloud Code Assist is authorized.

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
Headers on every call: `Authorization: Bearer <ya29>`, `Content-Type: application/json`,
`User-Agent: antigravity/<ver> <os>/<arch>` (e.g. `antigravity/1.15.8 windows/amd64`),
`X-Goog-Api-Client: …`, and a `Client-Metadata`/`metadata` object
`{"ideType":"ANTIGRAVITY","platform":"…","pluginType":"GEMINI","ideName":"antigravity",
"extensionName":"antigravity","locale":"en"}`.

| Purpose | Method | URL | Body | Key response fields |
|---|---|---|---|---|
| Project + plan discovery | POST | `…/v1internal:loadCodeAssist` | metadata (+ optional project) | `cloudaicompanionProject` (project id, needed by later calls), `currentTier`, `planInfo`, `availablePromptCredits` |
| Onboard (idempotent) | POST | `…/v1internal:onboardUser` | metadata | (project provisioning) |
| **Usage windows (grouped)** | POST | `…/v1internal:retrieveUserQuotaSummary` | metadata (+ project) | `response.groups[].{displayName, buckets[].{bucketId, displayName, description, remaining.remainingFraction, resetTime}}` |
| Usage (legacy/per-model) | POST | `…/v1internal:fetchAvailableModels` | `{"project":"<PROJECT_ID>"}` | `models[].{displayName, quotaInfo.{remainingFraction, resetTime, isExhausted}}` |
| Inference (429 source) | POST | `…/v1internal:streamGenerateContent?alt=sse` | Gemini request | emits the `Individual quota reached … Resets in Nh` 429 |

All `v1internal:` verbs are **POST-only** (Google gRPC-over-HTTP colon-method style),
so none are reachable under the GET-only research rule — the same situation as grok's
POST-only `grok.com/rest/rate-limits`.

## 3. Real captured payloads

**None captured.** The single live seed (`gm_antigravity_seeds`, `_id
6a4f018d420a406b02e7950c`, `blobId 6a4f018d420a406b02e7950a`) held an **expired**
access token: `expiry = 2026-07-09T05:03:43+02:00` (= `03:03:43Z`) vs research time
`03:11:24Z` — expired ~8 min earlier. `GET tokeninfo` confirmed
`{"error":"invalid_token"}`. Per the do-not-refresh rule (rotating single-use refresh
token) no live GET was issued; shapes above are derived from the wrapper code + vendor
docs (§7). The blob decrypted cleanly via `engine.credentials.decrypt_session_blob`
(age-encrypted in this DB, not plain gzip). Only **1** seed exists (task brief said 2).

## 4. Fixture

Not written — no live capture (step 4 is conditional on a successful capture, which
did not occur). A future capture against a live (unexpired) seed should write a
PII-scrubbed `packages/optio-antigravity/tests/fixtures/account_capture.json` holding
the `userinfo` + `loadCodeAssist` + `retrieveUserQuotaSummary` JSON (scrub
email/name/id/project-id; keep `remainingFraction`/`resetTime` numbers verbatim).

## 5. AccountInfo field mapping proposal

`analyze_account(access_token: str)` — the `ya29` bearer from
`store["token"]["access_token"]`. Fail-soft → `EMPTY` on any error (mirrors
claudecode). Identity is one GET; plan+usage are POSTs (see the caveat below).

| AccountInfo field | Source (JSON path) | Notes |
|---|---|---|
| `name` | `userinfo.name` | `given_name`+`family_name` also present |
| `email` | `userinfo.email` | `verified_email`/`email_verified` flags presence |
| `account_id` | `userinfo.id` (v1) / `userinfo.sub` (v3) | Google account id; `loadCodeAssist.cloudaicompanionProject` is an alternative (the CCA project id) |
| `plan` | prettified `loadCodeAssist.currentTier` (fallback `planInfo`) | `auth_method:"consumer"` ⇒ a consumer AI tier (free / AI Pro / AI Ultra). Strip enum prefix + title-case, like claudecode's tier prettifier |
| `windows` | `retrieveUserQuotaSummary.response.groups[].buckets[]` → one `UsageWindow` each | see per-window mapping below |
| `raw` | `{"userinfo":…, "loadCodeAssist":…, "quotaSummary":…}` | escape hatch |

**Per usage window (from `retrieveUserQuotaSummary`):**
- `label` = `f"{group.displayName}:{bucket.displayName}"` (e.g. `"Gemini Models:Weekly"`,
  `"Gemini Models:5-hour"`, `"Claude and GPT models:Weekly"`) — or `bucket.description`.
- `pct` = `(1 - bucket.remaining.remainingFraction) * 100` (remainingFraction 1→0% used,
  0→100% used/exhausted). Guard: when a bucket is fully restored the API **omits**
  `remainingAmount`/`remainingFraction` — treat a missing fraction as `1.0` (0% used).
- `resets_at` = `parse(bucket.resetTime)` (ISO-8601; legacy epoch-seconds tolerated).
- `model` = `group.displayName` (the per-model-GROUP scope: `"Gemini Models"` vs
  `"Claude and GPT models"`). This is group-scoped, not single-model.

**Alternative per-single-model windows (from `fetchAvailableModels`):** one window per
`models[]`: `label = model.displayName` (e.g. `"Gemini 3 Pro (High)"`),
`pct = (1 - quotaInfo.remainingFraction)*100` (or `100` if `quotaInfo.isExhausted`),
`resets_at = parse(quotaInfo.resetTime)`, `model = model.id or model.displayName`.
`retrieveUserQuotaSummary` is preferred (it is exactly Antigravity's own "Model Quota"
UI: the two groups × {weekly, 5-hour} windows); `fetchAvailableModels` is the
finer-grained fallback.

The "Individual quota reached. Resets in 121h" maps to the group bucket with
`remainingFraction == 0` and `resetTime ≈ now + 121h` (≈ the 7-day weekly window on the
exhausted Gemini group) ⇒ `pct = 100`, so `is_limited(...)` fires for that model group.

## 6. Usage / limits — exposed?

**Yes — but POST-only.** Unlike grok (no usable usage source at all), Antigravity DOES
surface per-group, per-window usage with reset times, via
`cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary` (grouped weekly/5-hour
buckets with `remainingFraction` + `resetTime`) and `:fetchAvailableModels` (per-model
`quotaInfo`). Both are **POST** `v1internal:` verbs and need a project id from
`:loadCodeAssist` first, so they are out of scope for GET-only research. If a follow-up
POST probe is authorized, `AccountInfo.windows` and `is_limited(...)` become fully
populatable for antigravity — a capability grok lacks. Identity (name/email/id) is a
plain GET and needs no such authorization.

## 7. Blockers / open items

1. **Live token expired — zero live capture.** The only seed's `ya29` token expired
   ~8 min before research; refresh is forbidden (rotating single-use refresh token).
   `capture_status = token_expired`. Re-run against a freshly-refreshed/live seed to
   capture real `userinfo` + quota JSON and write the fixture.
2. **Only 1 seed present** in `gm_antigravity_seeds` (brief said 2). The one present
   is expired.
3. **Plan + usage are POST-only** (`v1internal:loadCodeAssist` /
   `:retrieveUserQuotaSummary` / `:fetchAvailableModels`), and require a
   `cloudaicompanionProject` from `loadCodeAssist` before the quota calls. Populating
   `plan`/`windows` needs an explicit POST-probe authorization (same gate as grok's
   POST `rest/rate-limits`). Verify these accept the `ya29` bearer + the
   `Client-Metadata{ideType:ANTIGRAVITY,pluginType:GEMINI}` envelope, and confirm the
   exact `currentTier`/`planInfo` enum values for the consumer tier.
4. **Identity-only fast path is safe read-only now.** If shipping incrementally,
   `analyze_account` can populate name/email/account_id from `userinfo` (one GET) with
   `windows=()`, then add the POST quota calls once authorized — mirroring how grok
   shipped identity+plan with empty windows.
5. **Field-name confirmation pending live JSON.** `remaining.remainingFraction` vs a
   flat `remainingFraction`, `bucket.displayName` vs `bucket.description`, and whether
   `resetTime` is per-bucket or a legacy top-level field all vary across the third-party
   reimplementations surveyed (CodexBar, PicoClaw, opencode-antigravity-auth) — lock
   them against a real `retrieveUserQuotaSummary` response before coding the parser.

## 8. Environment / method (reproducibility)

- Mongo `mongodb://localhost:27017`, db `excavator`, coll `gm_antigravity_seeds`
  (1 seed; `_id 6a4f018d420a406b02e7950c`). Blob decrypted via
  `engine.credentials.decrypt_session_blob` (age-encrypted here), then the tar.gz
  member `.gemini/antigravity-cli/antigravity-oauth-token` parsed for the token store.
- Python `/home/csillag/deai/excavator/.venv/bin/python`,
  `EXCAVATOR_KEY_DIR=/home/csillag/excavator-trinkets`.
- Only read-only calls issued: `GET tokeninfo` (returned `invalid_token` — the
  expiry evidence). No `userinfo`/CCA calls (token dead). No Mongo writes, no token
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
