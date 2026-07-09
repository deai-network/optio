# optio-kimicode `analyze_account` — account-analysis research

**Date:** 2026-07-09
**Status:** research complete (read-only, CODE + DOCS only). No source built, no code edited.
**Capture status:** `no_seed` — NO live kimicode seed exists (login currently broken), so the live GET
capture was SKIPPED per task. Shapes below are derived from the vendor CLI source
(`~/deai/kimi-code`, the `@moonshot-ai/oauth` package) + Kimi docs. **Live verification is
pending a working seed.**
**Companion fixture:** none (no live capture; expected shape documented inline in §3).
**Reference analyzers:** `optio-claudecode/account.py`, `optio-codex/account.py`, `optio-cursor/account.py`.

## TL;DR — kimicode is the INVERSE of grok/cursor

- **Usage / limit windows: FULLY available read-only** via the seed's OAuth bearer, and the
  *only* account-shaped data the CLI token can reach:
  `GET https://api.kimi.com/coding/v1/usages` → a weekly-quota summary + rolling sub-windows
  (e.g. 5h), each with absolute `used` / `limit` counts + a reset time. So `AccountInfo.windows`
  is populated and `is_limited(...)` works for kimicode (unlike grok/cursor, whose windows are empty).
- **Identity (name / email / plan / account_id): NOT exposed** to the CLI OAuth token by any
  endpoint. The `/usages` payload carries no identity; the daemon's `GET /v1/auth` auth-summary
  carries only readiness (no user fields); the access token is treated as **opaque** by the CLI
  (never decoded for claims). So `AccountInfo.{name,email,plan,account_id}` will all be `None`, and
  therefore `AccountInfo.summary` (which requires plan AND email) will always be `None`.

Consequence: kimicode ships **usage gating, no identity summary** — the mirror image of grok/cursor
(identity + plan, no usage).

## 1. Credential layout (from the wrapper)

Source: `optio_kimicode/verify.py`, `seed_manifest.py`.

- **Seed-blob member holding creds:** `credentials/kimi-code.json` (`_CRED_MEMBER` in `verify.py`;
  rooted at the manifest `home_subdir="home"`, i.e. `KIMI_CODE_HOME = <workdir>/home`). The full
  seed tar.gz also carries `config.toml` (the `managed:kimi-code` provider registration — load-bearing
  for launch but NOT credential-bearing).
- **`kimi-code.json` shape:** a FLAT snake_case token dict (no `{issuer::client}` wrapper, unlike grok):
  `access_token` / `refresh_token` / `expires_at` (unix SECONDS) / `scope` / `token_type` / `expires_in`.
- **Access-token field:** `creds["access_token"]` — this is the bearer to pass to `analyze_account`.
  Format is **opaque** as far as the CLI is concerned (the vendor `TokenInfo` never decodes it; no
  identity claims are extracted anywhere). Whether it happens to be a JWT carrying identity claims is
  **unverified** (needs a real seed to inspect) — the CLI does not rely on it, so neither should the
  analyzer.
- **Live-host path (for a `resolve_capture_account` variant):** `<workdir>/home/credentials/kimi-code.json`.
- **Vendor auth used by verify:** OAuth token endpoint `POST https://auth.kimi.com/api/oauth/token`
  (public client `17e5f671-d194-4dfb-9706-5516cb48c098`, no secret), form-encoded `refresh_token` grant.
  **The analyzer must NOT refresh** (single-use rotating refresh token; a refresh without seed save-back
  strands the seed). kimi exposes no OIDC discovery and no userinfo liveness probe.

## 2. Vendor endpoints (reachability with the seed OAuth bearer)

The managed provider is `managed:kimi-code`; its base URL is `https://api.kimi.com/coding/v1`
(env override `KIMI_CODE_BASE_URL`). This host is DISTINCT from the Moonshot open-platform hosts
(`api.moonshot.ai/v1`, `api.moonshot.cn/v1`), which take a `sk-…` platform API key, NOT the OAuth token.

| Purpose | Method | URL | Reachable with seed token? | Identity? | Usage? |
|---|---|---|---|---|---|
| **Usage / limits** | GET | `https://api.kimi.com/coding/v1/usages` | **YES** (Bearer `access_token`) | no | **YES** |
| Model list | GET | `https://api.kimi.com/coding/v1/models` | yes | no | no (not needed for account) |
| Daemon auth summary | GET | `/v1/auth` (local daemon) | n/a (local) | **no** (`ready`, `providers_count`, `default_model`, `managed_provider.{name,status}` only) | no |
| Platform balance | GET | `https://api.moonshot.ai/v1/users/me/balance` | **NO** — needs a `sk-` platform API key, not the coding OAuth token; also returns only `available_balance`/`voucher_balance`/`cash_balance`, **no identity** | no | no (USD balance, not quota) |
| OAuth token (refresh) | POST | `https://auth.kimi.com/api/oauth/token` | yes | no | no — **do NOT call** (rotates single-use refresh token) |

Auth header for the reachable GETs (from `managed-usage.ts` / `managed-kimi-code.ts`):
`Authorization: Bearer <access_token>`, `Accept: application/json`. A custom UA is NOT required for
`api.kimi.com` (the CLI sends `X-Msh-*` device headers + a `kimi_code_cli` UA, but those are not
needed to authorize a GET; a plain UA like `optio-kimicode/account` should suffice — **live-unverified**).

## 3. `/coding/v1/usages` payload shape (from `managed-usage.ts`)

Documented in the vendor parser (`packages/oauth/src/managed-usage.ts`); the parser is deliberately
loose because field spelling drifted across versions. Canonical shape:

```json
{
  "usage": { "name": "Weekly limit", "used": 40, "limit": 1000, "resetAt": "2026-07-16T00:00:00Z" },
  "limits": [
    { "detail": {"used": 1, "limit": 100, "name": "5h limit"},
      "window": {"duration": 300, "timeUnit": "MINUTE"} }
  ]
}
```

Field tolerances the parser accepts (mirror these in the analyzer):

- **Counts:** `used` + `limit` are ABSOLUTE integers (NOT percentages — unlike codex/cursor which
  return `used_percent`). When `used` is absent, derive `used = limit - remaining`.
  → **pct MUST be computed:** `pct = 100 * used / limit` (guard `limit > 0`).
- **Name/label:** `name` → else `title` → else, for `limits[]`, a label synthesized from the window
  `duration` + `timeUnit` (`MINUTE`/`HOUR`/`DAY` → e.g. `300 MINUTE` → `"5h limit"`) → else `"Limit #N"`.
  The top-level `usage` summary defaults to `"Weekly limit"`.
- **Reset time:** first string among `reset_at` / `resetAt` / `reset_time` / `resetTime` (ISO-8601, may
  carry nano precision — trim fractional seconds to ms before parsing); else a relative-seconds field
  among `reset_in` / `resetIn` / `ttl` / `window` (→ `now + seconds`). May be absent → `resets_at = None`.
- **Per-model scoping:** NONE. kimi meters an account-wide membership quota shared across all models
  ("Kimi Code shares quota with the Kimi membership plan"), so every `UsageWindow.model = None`
  (global). There is no per-model window in this payload.

## 4. AccountInfo field mapping proposal

`analyze_account(creds)` where `creds = creds["access_token"]` (the bearer string) — mirror
cursor's signature (a token string in). One read-only GET (`/usages`), fail-soft to `EMPTY`.

| AccountInfo field | Source (JSON path) | Notes |
|---|---|---|
| `name` | — | **None.** No identity source reachable by the CLI token. |
| `email` | — | **None.** Same. |
| `plan` | — | **None.** No plan/tier/subscription field in any reachable payload. (Could hardcode a static `"Kimi Code"` label, but it carries no real tier and does not help `summary`, which also needs `email` — recommend leaving `None`.) |
| `account_id` | — | **None.** No account/user id in any reachable payload. |
| `windows` | from `/usages`: the `usage` summary (one window) + each `limits[]` entry | label per §3; `pct = 100*used/limit`; `resets_at` per §3; `model = None` for all |
| `raw` | `{"usage": <full /usages json>}` | escape hatch |

`AccountInfo.summary` → always `None` for kimicode (needs plan + email, both `None`). The
`on_seed_saved` 2nd arg will therefore be `None`; the value of kimicode's analyzer is the **usage
windows / `is_limited` gate**, not a human identity string.

Window-building sketch (mirror codex `_window_from` but compute pct from counts):

```
for each row in ([usage] + limits[*].detail):
    used, limit = row.used (or limit - row.remaining), row.limit
    if limit <= 0: skip
    pct = 100.0 * used / limit
    label = row.name or row.title or synth_from_window(duration,timeUnit) or default
    resets_at = parse_iso(row.reset_at|resetAt|reset_time|resetTime)  # trim nanos
               or now + (row.reset_in|resetIn|ttl|window seconds)
    model = None
```

## 5. Usage / limits — exposed?

**YES — read-only and reachable with the stored OAuth access token**, via
`GET https://api.kimi.com/coding/v1/usages`. This is kimicode's distinguishing strength versus the
other bearer-only wrappers: real per-account quota utilization + reset windows, so
`supports_usage_gating` can be **True** for kimicode and `is_limited(...)` is meaningful.

Caveat: kimi runs **two independent quota systems** (a 7-day weekly membership quota AND a rolling
~5h frequency window — see MoonshotAI/kimi-cli issue #2150); both surface in `limits[]`/`usage` and
both should become windows. All are account-wide (no per-model scoping).

## 6. Blockers / open items

1. **No live seed → nothing captured.** `capture_status = no_seed`. All shapes are code/docs-derived;
   the exact live `/usages` JSON (field casing actually served today, whether `resetAt` vs `reset_at`,
   presence of `window.duration/timeUnit`) is **unverified**. First follow-up when a seed exists: one
   read-only `GET /coding/v1/usages` with the stored token, then write a PII-scrubbed fixture to
   `packages/optio-kimicode/tests/fixtures/`.
2. **Identity is fundamentally unavailable** to the CLI OAuth token — no `/me`, no userinfo, no plan
   field, and the daemon auth-summary carries none. So `name/email/plan/account_id/summary` are all
   `None` unless a future kimi identity endpoint appears (or the access token turns out to be a JWT
   with identity claims — inspect a real token to check; the CLI itself never does).
3. **pct is computed, not read.** Unlike codex/cursor, kimi returns absolute `used`/`limit`; the
   analyzer must divide (and handle the `remaining` fallback + `limit == 0` guard).
4. **No token refresh** (single-use rotating refresh token; refresh without seed save-back kills the
   seed — per task rule). The analyzer uses the stored token as-is; an expired token → `EMPTY`
   (fail-soft), never a refresh.
5. **Platform balance endpoint is a red herring** for accounts: it needs a `sk-` platform key (not the
   coding OAuth token) and returns only USD balances, no identity.

## 7. Environment / method (reproducibility)

- Vendor source of truth: `~/deai/kimi-code/packages/oauth/src/` — `managed-usage.ts` (usage endpoint +
  payload parser), `managed-kimi-code.ts` (`DEFAULT_KIMI_CODE_BASE_URL`, models endpoint), `constants.ts`
  (OAuth host + client id), `open-platform.ts` (moonshot platform hosts), `token-state.ts` (`TokenInfo`
  shape), `identity.ts` (device/UA headers). Daemon auth summary: `packages/agent-core/src/services/
  authSummary/authSummary.ts` + `packages/protocol/src/rest/auth.ts`.
- Docs cited: Kimi API balance (`platform.kimi.ai/docs/api/balance`), Kimi Code usage/limits
  (`kimi.com/code/docs`, `platform.kimi.ai/docs/pricing/limits`), the two-quota-system UX issue
  (github.com/MoonshotAI/kimi-cli/issues/2150).
- No Mongo access, no HTTP calls, no token refresh, no source edits, no commits. Read-only research.
</content>
</invoke>
