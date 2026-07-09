# optio-cursor `analyze_account` — research notes

**Date:** 2026-07-09
**Status:** research only (no analyzer built, no source edited)
**Live capture:** succeeded against a real `gm_cursor_seeds` seed (token valid, read-only GETs).
**Fixture:** `packages/optio-cursor/tests/fixtures/account_capture.json` (PII-scrubbed).

## 1. Credential layout (from the wrapper code)

- **Seed-blob member path:** `.config/cursor/auth.json` (tar.gz member; manifest
  `home_subdir="home"`, so on-host it is `<workdir>/home/.config/cursor/auth.json`).
  Sources: `seed_manifest.py` (`CURSOR_CRED_MANIFEST.include`), `verify.py`
  (`_AUTH_MEMBER = ".config/cursor/auth.json"`), `cred_watcher.py`.
- **auth.json shape:** flat object `{"accessToken": <jwt>, "refreshToken": <jwt>}`.
  Nothing else (confirmed against the real seed).
- **Token field the analyzer consumes:** `accessToken`.
- The wrapper never calls a vendor account/usage API today — `verify.py` proves
  liveness with a `cursor-agent -p` challenge probe, not an HTTP call. So the
  vendor API surface below is **new research**, not mirrored from existing code.

### accessToken is a WorkOS/Auth0 session JWT (decoded, not verified)

`alg=HS256`. Claims: `sub`, `time`, `randomness`, `exp`, `iss`, `scope`, `aud`,
`type`. Concretely from the live seed:

- `iss = https://authentication.cursor.sh`
- `aud = https://cursor.com`
- `type = session`
- `exp` ≈ 60 days out (this seed: minted 2026-07 → exp 2026-09-06); **valid at
  capture**.
- `sub = google-oauth2|user_01KNVAV1XB2WHSK7NN2D0PY5SQ` — the **cookie userId**
  (see below). Note the `/api/auth/me` body returns the *bare* `user_01…` form in
  its own `sub` field; the JWT `sub` carries the `google-oauth2|` prefix. Use the
  **JWT `sub` verbatim** for the cookie.

## 2. Vendor API surface

Cursor exposes **no official personal usage/profile API** (official API keys are
Admin-only, Cloud-Agents-scoped — [Cursor API docs](https://cursor.com/docs/api),
[CLI auth](https://cursor.com/docs/cli/reference/authentication)). The dashboard's
own internal `cursor.com/api/*` endpoints are what the community usage extensions
call ([cursor-usage-tracker](https://github.com/Tendo33/cursor-usage-tracker),
[cursor-stats](https://github.com/Dwtexe/cursor-stats)). They authenticate with the
**browser session cookie**, not a Bearer header.

### Auth: cookie, not Bearer

```
Cookie: WorkosCursorSessionToken=<jwt.sub>%3A%3A<accessToken>
```

i.e. `<sub>::<accessToken>` URL-encoded. **Verified:** `GET /api/auth/me` with the
cookie → `200` + JSON; the same call with `Authorization: Bearer <accessToken>` →
`204` (empty). The dashboard endpoints only honour the cookie. The `%3A%3A` (`::`)
separator and the `.sub` prefix are both load-bearing (the cookie built from the
bare `user_01…` form is untested — use the JWT `sub` as captured).

### Endpoints captured (all HTTP GET, cookie auth, read-only)

| Purpose | Method | URL | Result |
|---|---|---|---|
| Identity (name/email/id) | GET | `https://cursor.com/api/auth/me` | 200 JSON ✅ |
| Plan / billing | GET | `https://cursor.com/api/auth/stripe` | 200 JSON ✅ |
| **Usage windows + reset** | GET | `https://cursor.com/api/usage-summary` | 200 JSON ✅ |
| Legacy per-model usage | GET | `https://cursor.com/api/usage?user=<jwt.sub>` | 200 JSON (empty for this acct) |
| On-demand-billing flag | GET | `https://cursor.com/api/dashboard/get-hard-limit` | 200 JSON `{"noUsageBasedAllowed":true}` |
| — | GET | `.../api/auth/full_stripe_profile`, `.../api/dashboard/user-usage` | returns SPA HTML, **not** JSON GET APIs (POST/page routes) |

No custom `User-Agent` or extra header is required (a plain UA worked). Community
tools sometimes add `Connect-Protocol-Version: 1`, but only for the `api2.cursor.sh`
gRPC-web POST RPCs, which were **not** called here (GET-only rule).

## 3. Real captured payloads (scrubbed in fixture; shapes verbatim)

`GET /api/auth/me`:
```json
{"email":"…","email_verified":true,"name":"…","sub":"user_01…",
 "created_at":"…","updated_at":"…","picture":"…","id":349352205}
```

`GET /api/auth/stripe`:
```json
{"membershipType":"free","individualMembershipType":"free","isTeamMember":false,
 "teamMembershipType":null,"isOnBillableAuto":true,"isYearlyPlan":false,
 "trialEligible":true,"paymentId":"cus_…", …}
```

`GET /api/usage-summary` (**the usage/limit source**):
```json
{"billingCycleStart":"2026-06-10T09:16:18.140Z",
 "billingCycleEnd":"2026-07-10T09:16:18.140Z",
 "membershipType":"free","limitType":"user","isUnlimited":false,
 "autoModelSelectedDisplayMessage":"You've used 100% of your included total usage",
 "namedModelSelectedDisplayMessage":"You've used 82% of your included API usage",
 "individualUsage":{
   "plan":{"enabled":true,"used":0,"limit":0,"remaining":0,
     "breakdown":{"included":0,"bonus":219,"total":219},
     "autoPercentUsed":100,"apiPercentUsed":82,"totalPercentUsed":100},
   "onDemand":{"enabled":false,"used":0,"limit":null,"remaining":null}},
 "teamUsage":{}}
```

`GET /api/usage` (legacy) — empty/deprecated for this account:
```json
{"gpt-4":{"numRequests":0,"numRequestsTotal":0,"numTokens":0,
 "maxTokenUsage":null,"maxRequestUsage":null},
 "startOfMonth":"2026-06-10T09:16:18.140Z"}
```

## 4. `AccountInfo` field mapping proposal

`analyze_account(creds)` where `creds` = the `accessToken` string (extraction of
`accessToken` from auth.json is the call-path's job, per the design doc). Steps:
decode JWT `sub` (unverified b64 of the middle segment) → build the cookie → three
GETs → map.

| AccountInfo field | Source JSON path | Notes |
|---|---|---|
| `name` | `auth/me` → `.name` | `null` → `None` |
| `email` | `auth/me` → `.email` | |
| `account_id` | `auth/me` → `.sub` (bare `user_01…`) | canonical WorkOS user id. (JWT `sub` = `google-oauth2\|user_01…` is the cookie key, not the account_id.) |
| `plan` | `auth/stripe` → `.membershipType` | e.g. `"free"`, `"pro"`, `"business"`. Prefer `.individualMembershipType` if a team/individual split matters; `usage-summary.membershipType` is a fallback if the stripe GET fails. Optionally prettify (`"free"`→`"Free"`). |
| `raw` | `{"me":…, "stripe":…, "usage":…}` | escape hatch, mirrors claudecode |

### UsageWindows (from `/api/usage-summary` → `individualUsage.plan`)

Cursor's personal usage is **plan-bucket percentages, not per-model** — three
utilization figures against one monthly billing cycle. Map each present percent to
one `UsageWindow`, all `model=None`, all sharing `resets_at = billingCycleEnd`:

| UsageWindow | `label` | `pct` (path) | `resets_at` | `model` |
|---|---|---|---|---|
| total incl. usage | `"total"` | `individualUsage.plan.totalPercentUsed` | `billingCycleEnd` | `None` |
| auto-model usage | `"auto"` | `individualUsage.plan.autoPercentUsed` | `billingCycleEnd` | `None` |
| named/API usage | `"api"` | `individualUsage.plan.apiPercentUsed` | `billingCycleEnd` | `None` |

- `resets_at` = `datetime.fromisoformat(billingCycleEnd)` (ISO-8601 `Z` → parse to
  tz-aware; the `Z` suffix may need `.replace("Z","+00:00")` on older Pythons).
- `pct` is already 0–100 (matches `UsageWindow.pct` contract). This account shows
  `total=100, auto=100, api=82` → `is_limited(info, now)` would return **True** for
  the `total`/`auto` windows (pct ≥ 100 and `resets_at` in the future) — exactly the
  "included usage exhausted" state, which is what the design wants to detect.
- **Per-model scoping:** none available from `usage-summary`. The legacy
  `/api/usage` has per-model keys (`gpt-4`, etc.) with `numRequests` /
  `maxRequestUsage`, but they read `0`/`null` on current plans (deprecated request-
  quota model). Do **not** build per-model windows from it — treat cursor as a
  global-window-only vendor. This matches the `verify.py`/`cred_watcher.py` note
  that "cursor has no separate model gate."
- `onDemand` (usage-based billing): `enabled=false` here. When enabled with a
  `limit`, `used/limit*100` could add an `"on_demand"` window; optional, not needed
  for the limited-gate. `get-hard-limit.noUsageBasedAllowed` corroborates whether
  on-demand is even permitted.

## 5. Answers to the brief

- **Usage/limits exposed?** Yes — `GET /api/usage-summary` gives real percentages
  (`autoPercentUsed`/`apiPercentUsed`/`totalPercentUsed`) and a hard reset boundary
  (`billingCycleEnd`). This is a monthly billing-cycle window, **not** the rolling
  5h/7h windows claude has; cursor has one reset per cycle.
- **Per-model windows?** No. Cursor meters plan buckets (auto vs named-API vs
  total), not per-model. `model` is always `None`.
- **Reset times?** One: `billingCycleEnd` (monthly). Apply it to every window.
- **Identity source?** `GET /api/auth/me` (cookie) — name, email, and the bare
  WorkOS `user_…` id.
- **Auth quirk (important for the builder):** cookie auth only. Bearer returns 204.
  Cookie value = `WorkosCursorSessionToken=<jwt.sub>::<accessToken>` URL-encoded,
  and the `sub` must be the JWT's `google-oauth2|user_…`, not the me-body `user_…`.

## 6. Blockers / caveats

- **None blocking the analyzer build.** All required data is reachable via read-only
  GET with the stored (unrotated) token.
- Endpoints are **unofficial** dashboard internals — no stability guarantee; fail-
  soft (`→ EMPTY`) is mandatory, as the design already requires.
- The richer gRPC-web usage RPCs
  (`POST api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage`) were
  **not** exercised (POST, out of the GET-only research scope). `/api/usage-summary`
  already yields everything `AccountInfo` needs, so the POST RPC is not required for
  the build; noted only as an alternate/higher-fidelity source if `usage-summary`
  ever regresses.
- **Deployment note:** in this excavator DB the `gm_cursor_seeds` blob was stored
  **un-encrypted** (plain `tar.gz`, gzip magic `1f 8b`) — `decrypt_session_blob`
  raised `Header is invalid`; reading the tar directly worked. Existing
  `metadata.account` on the seed is the legacy empty `{"uuid":null,"summary":null}`
  shape (a prior verify ran with no cursor analyzer), confirming this capability is
  currently absent for cursor.
