# Codex (OpenAI/ChatGPT) account-analysis research

**Date:** 2026-07-09
**Status:** research complete — real payloads captured from a live seed (read-only)
**Scope:** inputs for a future `optio_codex.account.analyze_account(creds) -> AccountInfo`.
No analyzer built, no source edited (per the research-phase brief).

Mirrors the reference `optio_claudecode` analyzer (`account.py` + `oauth.py`):
a "profile" identity source + a "usage" window source, both fail-soft, mapped
into the shared `optio_agents.account.AccountInfo` / `UsageWindow`.

## 1. Credential layout (from `optio_codex.verify` / `seed_manifest`)

- Seed-blob member: **`.codex/auth.json`** (`_AUTH_MEMBER`), under `home_subdir="home"`
  (live workdir relpath `home/.codex/auth.json`).
- `auth.json` shape (ChatGPT mode):
  ```json
  {"auth_mode":"chatgpt", "OPENAI_API_KEY":null,
   "tokens":{"id_token":"<JWT>","access_token":"<JWT>",
             "refresh_token":"<opaque>","account_id":"<uuid>"},
   "last_refresh":"<RFC3339 nanosecond>"}
  ```
- **Access token field:** `tokens.access_token` — a **real JWT** (`aud:
  ["https://api.openai.com/v1"]`, `iss: https://auth.openai.com`), the bearer for
  all vendor GETs below. The seed I captured had `last_refresh 2026-07-08T23:40Z`
  and an access-token `exp` ~10 days out → **valid, non-expired**.
- **`tokens.id_token`** is a second JWT that carries identity claims **offline**
  (name/email/plan/account uuid) — the only source of `name` short of a live call.
- **`tokens.account_id`** = the ChatGPT **account uuid** (`38a3…`-style), needed
  both for the `ChatGPT-Account-Id` request header and for `AccountInfo.account_id`.
- API-key seeds (`OPENAI_API_KEY` set, `tokens` null): no OAuth identity/usage —
  analyzer should return `EMPTY` (nothing to fetch).

**Recommended `creds` form:** pass the whole **`tokens` dict** (not just the
access token). Rationale — a divergence from claudecode: codex needs `id_token`
(for `name`), `access_token` (bearer), and `account_id` (header + `account_id`
field). Extract it from the seed via the existing `verify._read_auth` →
`auth["tokens"]`, or in the capture path from the live `home/.codex/auth.json`.

## 2. Vendor endpoints (all verified live, read-only GET, HTTP 200)

Base host **`https://chatgpt.com`** (the ChatGPT backend, not `api.openai.com`).
Common headers: `Authorization: Bearer <tokens.access_token>`,
`ChatGPT-Account-Id: <tokens.account_id>`, `User-Agent: codex_cli_rs/<ver>`
(a real UA is **required** — chatgpt.com serves a Cloudflare HTML challenge to a
blank/curl UA), `Accept: application/json`.

| Purpose | Method + URL | Notes |
|---|---|---|
| **Usage / rate-limits** (windows) | `GET https://chatgpt.com/backend-api/wham/usage` | Primary source. The codex TUI polls this every 60s (`ChatWidget::prefetch_rate_limits` → `backend-client get_rate_limits`). **Read-only, non-billable.** |
| **Identity (live)** | `GET https://chatgpt.com/backend-api/me` | Returns `name`, `first_name`, `email`, `id` (user-id), `orgs[]`. Live source for `name`. |
| Reset-credit inventory | `GET https://chatgpt.com/backend-api/wham/rate-limit-reset-credits` | `{available_count,total_earned_count,credits[]}`. Not needed for `AccountInfo`; informational. |
| Account/entitlement detail | `GET https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27` | Rich plan/entitlement/subscription blob keyed by account uuid (`plan_type`, `entitlement.subscription_plan`, `has_active_subscription`). Overkill for the current field set; good `raw` escape-hatch fallback. |

The billable inference path (`POST …/codex/responses`) also returns rate-limit
info in headers/SSE `token_count` events, but that is **not read-only** — the
`wham/usage` GET is the correct, non-billable source and is what the analyzer
should use.

## 3. Real captured payload — `GET /backend-api/wham/usage`

Captured from the live free-plan seed (scrubbed fixture:
`packages/optio-codex/tests/fixtures/codex_wham_usage_free.json`; usage numbers
verbatim):

```json
{
  "user_id": "user-…", "account_id": "user-…",   // NB: this is the USER id, not the uuid
  "email": "user@example.com",
  "plan_type": "free",
  "rate_limit": {
    "allowed": true, "limit_reached": false,
    "primary_window":   {"used_percent": 68, "limit_window_seconds": 2592000,
                          "reset_after_seconds": 1090890, "reset_at": 1784650946},
    "secondary_window": null,
    "code_review_rate_limit": null,
    "additional_rate_limits": null
  },
  "credits": {"has_credits": false, "unlimited": false, "overage_limit_reached": false,
              "balance": null, "approx_local_messages": null, "approx_cloud_messages": null},
  "spend_control": {"reached": false, "individual_limit": null},
  "rate_limit_reached_type": null,
  "rate_limit_reset_credits": {"available_count": 0}
}
```

Window struct (each of `primary_window` / `secondary_window` / entries of
`additional_rate_limits[]`):
- `used_percent` — 0..100 utilization (float/int).
- `limit_window_seconds` — window length. Free plan here = `2592000` (30d, a single
  monthly bucket). Paid plans: **primary = 5-hour session** (`18000`), **secondary
  = weekly** (`604800`).
- `reset_after_seconds` — seconds until reset (relative).
- `reset_at` — **unix epoch seconds** (absolute); preferred for `resets_at`.

Per plan (from codex source PR #11260 "support multiple rate limits" + CodexBar
reverse-engineering): `secondary_window` and `additional_rate_limits[]` are
**null on free** (as captured) and populated on paid plans.
`additional_rate_limits[]` carries **per-model** limits (e.g. a
"GPT-5.3-Codex-Spark" lane) → these map to per-model `UsageWindow.model`. Each
`additional_rate_limits` entry has the same window fields plus a name/id label.

## 4. Identity payload — `GET /backend-api/me` + id_token claims

`/backend-api/me` (scrubbed fixture `codex_backend_me.json`):
`{"object":"user","id":"user-…","email":"user@example.com","name":"Ada Lovelace",
"first_name":"Ada","orgs":{…},"client_id":"app_EMoamEEZ73f0CkXaXp7hrann",…}`.
Note: `me.id` is the **user id** (`user-…`), NOT the account uuid.

`tokens.id_token` claims (scrubbed fixture `codex_id_token_claims_free.json`,
decoded offline — no network):
```json
{"email":"user@example.com", "name":"Ada Lovelace", "email_verified":true,
 "iss":"https://auth.openai.com", "aud":["app_EMoamEEZ73f0CkXaXp7hrann"],
 "https://api.openai.com/auth":{
   "chatgpt_account_id":"<uuid>", "chatgpt_plan_type":"free",
   "chatgpt_user_id":"user-…", "organizations":[{…}]}}
```
The id_token alone yields name+email+plan+account uuid with **zero network** —
useful as a fast/offline path and as a fail-soft fallback when the live GET fails.

## 5. AccountInfo field mapping (proposal)

| AccountInfo field | Source (primary → fallback) | JSON path |
|---|---|---|
| `name` | id_token (offline) → live `me` | id_token `.name` → `GET /me .name` |
| `email` | wham/usage → id_token → `me` | `usage.email` → id_token `.email` → `me.email` |
| `plan` | wham/usage (live, authoritative) → id_token | `usage.plan_type` → id_token `["https://api.openai.com/auth"].chatgpt_plan_type`, **prettified** (see below) |
| `account_id` | creds (the account **uuid**) | `tokens.account_id` (≡ id_token `…chatgpt_account_id`). **Do NOT use `usage.account_id`** — that field is the user-id string, not the uuid |
| `windows` | wham/usage `.rate_limit` | see below |

**Plan prettifier** (`plan_type` → display), mirroring claudecode's `_format_plan`:
`free→"Free"`, `plus→"ChatGPT Plus"`, `pro→"ChatGPT Pro"`, `team→"ChatGPT Team"`,
`business/enterprise/edu→…`; unknown tokens pass through title-cased. (Exact paid
token spellings to be confirmed against a paid seed; only `free` observed live.)

**Windows** — from `usage.rate_limit`, each non-null window → one `UsageWindow`:
- `primary_window`  → `UsageWindow(label="primary",   pct=used_percent, resets_at=utc(reset_at), model=None)`
- `secondary_window`→ `UsageWindow(label="secondary", pct=used_percent, resets_at=utc(reset_at), model=None)` (skip when null)
- `additional_rate_limits[]` → per entry `UsageWindow(label=<entry name>, pct=…, resets_at=utc(reset_at), model=<entry model/name>)` (skip when null)
- `code_review_rate_limit` → optional extra global window when non-null.
- `resets_at` = `datetime.fromtimestamp(reset_at, tz=timezone.utc)`; fallback
  `now + timedelta(seconds=reset_after_seconds)` if `reset_at` absent.
- (Optional richer labels: derive from `limit_window_seconds` — 18000→"5h",
  604800→"weekly", 2592000→"monthly" — but "primary"/"secondary" is safe and
  plan-agnostic; keep the full payload in `AccountInfo.raw`.)

`AccountInfo.raw` = `{"usage": <wham/usage json>, "me"|"id_token": <identity>}`,
matching claudecode's `{"profile":…, "usage":…}` convention.

## 6. Usage/limits exposure — YES

Codex **does** expose usage/limits via a **read-only, non-billable GET**
(`/backend-api/wham/usage`), unlike the "headers-only" impression from vendor docs.
Limit windows exposed: a **primary** window (5h session on paid / monthly on free),
a **secondary** weekly window (paid only), **per-model** `additional_rate_limits`
(paid only), and an optional `code_review_rate_limit`. Reset times exposed as both
absolute (`reset_at`, unix epoch) and relative (`reset_after_seconds`).
This satisfies the design's model-aware `is_limited` gate:
- global windows (`model=None`): primary/secondary/code_review.
- per-model windows: `additional_rate_limits[]` → `UsageWindow.model`.

## 7. Blockers / open items

- **None blocking.** Capture succeeded read-only; token valid; fixtures written.
- **Free-plan-only live sample.** The one available seed is `plan_type:"free"`, so
  `secondary_window` and `additional_rate_limits[]` were `null` — their exact
  populated shape (esp. the per-model label field name in `additional_rate_limits`)
  is documented from codex source (PR #11260) / CodexBar, **not** captured live.
  Confirm against a paid (Plus/Pro) seed before finalizing per-model `model=` and
  the plan prettifier's paid tokens.
- **`creds` shape divergence** from claudecode (needs the `tokens` dict, not a bare
  access token) — flag when wiring `verify_and_refresh_seed` / the capture path so
  the extracted creds include `id_token` + `account_id`, not just `access_token`.
- **UA required:** vendor GETs to `chatgpt.com` must send a non-empty `User-Agent`
  (Cloudflare). Use a `codex_cli_rs/<ver>`-style UA.

## Fixtures written (PII-scrubbed; usage numbers verbatim)

- `packages/optio-codex/tests/fixtures/codex_wham_usage_free.json` — the window source.
- `packages/optio-codex/tests/fixtures/codex_backend_me.json` — live identity (`name`).
- `packages/optio-codex/tests/fixtures/codex_id_token_claims_free.json` — offline id_token claims.

## Sources

- codex `wham/usage` polling + window fields: reverse-engineering in
  <https://github.com/steipete/CodexBar/blob/main/docs/codex.md> and codex issues
  <https://github.com/openai/codex/issues/29618>, <https://github.com/openai/codex/issues/10869>.
- multi/per-model limits (`additional_rate_limits`): PR
  <https://github.com/openai/codex/pull/11260>, issue
  <https://github.com/openai/codex/issues/15281>.
- ChatGPT-plan usage windows (5h session / weekly): OpenAI Help Center
  <https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan>.
- OIDC/auth.json/refresh facts: `optio_codex/verify.py` module docstring (Task 0, codex-cli 0.142.5).
