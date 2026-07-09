# optio-opencode `analyze_account` — account-analysis research

**Date:** 2026-07-09
**Status:** research only (no analyzer built, no source edited, read-only GETs).
**Companion fixture:** `packages/optio-opencode/tests/fixtures/account_xai_apikey.json`
**Reference analyzers mirrored:** `optio_codex/account.py` (OpenAI/ChatGPT OAuth),
`optio_grok` research (`docs/2026-07-09-grok-account-research.md`).

## TL;DR — opencode has NO native account/usage concept

opencode is a model-selection TUI over **BYO (bring-your-own) providers**. There
is no "opencode account", no opencode login, and `verify.py` proves liveness by
running the binary against a challenge prompt (`What is the capital of France?`),
**not** by calling any vendor API. A seed carries a **MODEL + a provider
credential**, not an opencode identity — `verify_and_refresh_seed` returns
`{alive, account=None, model}` and `account` is hard-coded `None`.

Therefore `analyze_account` for opencode is **not one vendor**. Any identity/usage
comes entirely from the **underlying provider** named as the key in `auth.json`,
and its shape depends on the credential **type** (`api` vs `oauth`) and **which
provider**. The right design is a **dispatcher**: read the single provider entry,
branch on `(provider, type)`, and delegate to the matching per-provider logic
(reusing codex's for openai-oauth, grok's for xai-oauth). For most credential
types the reachable `AccountInfo` is partial or `EMPTY`.

## 1. Credential layout (from the wrapper code)

Sources: `optio_opencode/verify.py`, `seed_manifest.py`.

- **Seed-blob member holding creds:** `.local/share/opencode/auth.json`
  (`verify._AUTH_MEMBER`; `OPENCODE_CRED_MANIFEST.include`). Under
  `home_subdir="home"`, so on-host relpath is
  `home/.local/share/opencode/auth.json`.
- The seed tar.gz also carries `.config/opencode/opencode.json` (holds `model` /
  `small_model` — the selected model, **not** a credential) and
  `.config/opencode/plugins`.
- **`auth.json` shape:** a dict **keyed by provider name**, one entry per
  authed provider:
  ```json
  { "<provider>": { "type": "oauth" | "api", ... } }
  ```
  Each entry is either:
  - `type: "api"` → `{ "type": "api", "key": "<api-key-string>" }`
  - `type: "oauth"` → `{ "type": "oauth", "access": "<JWT>",
    "refresh": "<opaque>", "expires": <ms-epoch>, "accountId"?: "<uuid>" }`
    (`accountId` present for openai, absent for xai).
- **Token/creds field the analyzer consumes:** the whole provider entry.
  For oauth the bearer is `entry["access"]`; for api the credential is
  `entry["key"]`. `entry["expires"]` is **milliseconds** epoch.
- The wrapper NEVER calls a vendor account/usage API today. The vendor surface in
  §3 is **new research**, not mirrored from existing opencode code.

### The 5 live seeds (surveyed read-only)

| blobId | status | model | provider | type | token state |
|---|---|---|---|---|---|
| 6a1d044c… | (none) | — | — | — | no auth.json (empty seed) |
| 6a1d0775… | (none) | — | — | — | no auth.json (empty seed) |
| 6a1d0a52… | (none) | xai/grok-4.3 | xai | **api** | api key (no expiry) — **LIVE** |
| 6a38bbf5… | alive | openai/gpt-5.4-mini | openai | **oauth** | `access` **EXPIRED** (exp 2026-06-30) |
| 6a38bb98… | alive | xai/grok-4.3 | xai | **oauth** | `access` **EXPIRED** (exp 2026-06-21) |

Both OAuth seeds' access tokens are **expired**; per the read-only brief they were
**not refreshed** (single-use refresh tokens rotate upstream; refreshing here
would strand the seed). Their shape below is derived from **offline JWT decode +
docs + the codex/grok references**. The **xai api-key** seed is live and yielded a
**real GET payload** (§3).

## 2. Per-provider account/usage reachability

### (a) provider `openai`, `type: oauth` — ChatGPT login (mirrors codex)

The `access` token is a real OpenAI OAuth JWT
(`iss=https://auth.openai.com`, `aud=["https://api.openai.com/v1"]`). Decoded
**offline** (no network, no refresh) it carries:

- `https://api.openai.com/auth` → `chatgpt_account_id` (uuid),
  `chatgpt_plan_type` (e.g. `"free"`), `chatgpt_user_id`, `user_id`.
- `https://api.openai.com/profile` → `email`, `email_verified`.

So **offline from the stored `access` token alone**: `email`, `plan`,
`account_id` are all available. **`name` is NOT** — unlike codex, opencode does
**not** store an `id_token` (the openai entry fields are exactly
`{type, access, refresh, expires, accountId}`), and the access-token profile
claim has only `email`. `name` would need the live
`GET https://chatgpt.com/backend-api/me`.

**Usage windows:** reachable via the **same** read-only, non-billable endpoint
codex uses — `GET https://chatgpt.com/backend-api/wham/usage` with
`Authorization: Bearer <access>`, `ChatGPT-Account-Id: <accountId>`,
`User-Agent: codex_cli_rs/<ver>` (a real UA is required — chatgpt.com serves a
Cloudflare challenge to a blank UA), `Accept: application/json`. Payload +
mapping are documented in `docs/2026-07-09-codex-account-research.md`
(`rate_limit.primary_window` / `secondary_window` / `additional_rate_limits[]`
→ `UsageWindow`). **Could not re-verify live** — this seed's token is expired.

Confirmed: the stored `accountId` field **== the access token's
`chatgpt_account_id`** claim.

### (b) provider `xai`, `type: oauth` — Grok login (mirrors grok)

The `access` token is an xAI OAuth JWT (`iss=https://auth.x.ai`,
`aud=b1a00492-073a-47ea-816f-4c329264a828`,
`scope="openid profile email offline_access grok-cli:access api:access"`,
`tier=1`, `principal_type`, `principal_id`, `team_id`). Same claim family the grok
research documents. **No email/name offline** (unlike opencode's openai token,
the xai token carries no profile/email claim). Per grok research, identity comes
from the OIDC `userinfo` endpoint (needs a **live** token) and plan from
`grok.com/rest/subscriptions`; **usage windows are NOT exposed** via any GET
reachable with this bearer (`grok.com/rest/rate-limits` is POST-only). **Could not
verify live** — token expired.

### (c) provider `xai`, `type: api` — BYO xAI API key — **LIVE, captured**

`GET https://api.x.ai/v1/api-key` with `Authorization: Bearer <key>` → **200**
(real payload in §3). It returns **key/team metadata only**: `user_id`, `team_id`,
`api_key_id`, `redacted_api_key`, `name` (the **API-key label**, e.g. `"Default"`
— NOT a person name), `acls[]`, and the `team_blocked` / `api_key_blocked` /
`api_key_disabled` flags. **No email, no person name, no plan, no usage windows.**
`GET /v1/models` and `/v1/language-models` returned **403 `permission-denied`**
("team … has used all available credits or reached its monthly spending limit") —
consistent with `team_blocked:true`; that message is a coarse billing signal, not
a structured usage window. No per-user usage/rate-limit GET is exposed for an
API-key principal.

### (d) other providers / `type: api` for anthropic/openai/etc.

General case for a raw provider **API key**: no OAuth identity, no personal
usage/profile GET tied to the key (Anthropic/OpenAI keys don't expose per-user
usage via a simple bearer GET). → `EMPTY`.

## 3. Real captured payload (LIVE, read-only) — `GET /v1/api-key`

xAI api-key seed, scrubbed fixture `account_xai_apikey.json` (UUIDs + redacted key
→ placeholders; flags/acls verbatim):

```json
{"redacted_api_key":"xai-...XXXX",
 "user_id":"<uuid>", "name":"Default",
 "create_time":"", "modify_time":"", "modified_by":"<uuid>",
 "team_id":"<uuid>", "acls":["api-key:model:*","api-key:endpoint:*",""],
 "api_key_id":"<uuid>",
 "team_blocked":true, "api_key_blocked":false, "api_key_disabled":false}
```

The two OAuth seeds produced **no live payload** (expired tokens, refresh
forbidden); their access-token claim shapes are documented in §2 from offline
decode.

## 4. Proposed `AccountInfo` mapping (dispatcher)

`analyze_account(creds)` where `creds` = the single `auth.json` provider entry
`{provider, ...}` (or the whole `auth.json` + a chosen provider). Branch on
`(provider, type)`:

| case | name | email | plan | account_id | windows | notes |
|---|---|---|---|---|---|---|
| **openai/oauth** | `me.name` (live GET) else `None` | `access` JWT `…/profile.email` | `…/auth.chatgpt_plan_type` (→ codex `_PLAN_NAMES`) | `accountId` (== `chatgpt_account_id`) | codex `wham/usage` → `rate_limit.*` → `UsageWindow` (global + per-model `additional_rate_limits[]`; `resets_at` from `reset_at` epoch-s, else now+`reset_after_seconds`) | reuse `optio_codex.account` logic; no stored id_token, so name is live-only |
| **xai/oauth** | `userinfo` (live) else `None` | `userinfo`/`subscriptions` (live) else `None` | `grok.com/rest/subscriptions.tier` (live) else `None` | `principal_id`/`team_id` from JWT | **none** (grok usage not GET-exposed) | reuse grok research; identity needs a live token |
| **xai/api** | `None` (key label only, not a person) | `None` | `None` | `user_id` (or `team_id`) from `/v1/api-key` | **none** | `raw` = `/v1/api-key` payload; `team_blocked`/`api_key_blocked` are coarse status only |
| **other/api** | `None` | `None` | `None` | `None` | none | → `EMPTY` |

`UsageWindow` fields (openai/oauth case, per codex mapping):
`label` = `"primary"`/`"secondary"`/`"code_review"`/per-model name;
`pct` = `used_percent`; `resets_at` = `datetime.fromtimestamp(reset_at, utc)`
(fallback `now + reset_after_seconds`); `model` = `None` for global windows, set
from each `additional_rate_limits[]` entry for per-model windows.

`.summary` (`"Plan: <plan> for <name> <<email>>"`) is only non-None when both plan
and email resolve → in practice **only the openai/oauth path** can produce a
summary (and only if it decodes email + plan, which it does offline).

## 5. Blockers / open items

- **No live OAuth capture.** Both oauth seeds' `access` tokens are **expired** and
  refresh is forbidden read-only, so the openai `wham/usage` window payload and
  the xai `userinfo`/`subscriptions` identity payloads were **not re-verified for
  opencode** (they are verified in the codex/grok research docs against those
  wrappers' own live seeds). A fresh opencode oauth seed (or an authorized
  refresh) is needed to capture them under opencode.
- **No unified account exists.** opencode's `AccountInfo` is inherently
  per-provider and often partial/EMPTY. `is_limited` will be `False` for every
  case except openai/oauth (the only path with usage windows).
- **`name` for openai/oauth is live-only.** opencode stores no `id_token`, so an
  offline analyzer yields `name=None`; add the `GET /backend-api/me` call (as
  codex does) if `name` is wanted.
- **API-key providers give no usage.** The `/v1/api-key` metadata is the ceiling
  for xai/api; anthropic/openai API keys give nothing. The 403 billing message is
  not a structured window.
