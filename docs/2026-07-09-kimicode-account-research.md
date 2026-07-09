# optio-kimicode `analyze_account` — account-analysis research

**Date:** 2026-07-09
**Status:** research complete + LIVE-VERIFIED (read-only). No source built, no code edited.
**Capture status:** `captured` — a working kimicode seed now exists (login was fixed) and the live
`GET /coding/v1/usages` returned **200 with the stored token**. The shapes below are the REAL served
payload, not code-derived guesses. The earlier revision of this doc (`no_seed`) was code/docs-derived;
several of its conclusions were WRONG and are corrected below (see the ⚠ markers).
**Companion fixture:** `packages/optio-kimicode/tests/fixtures/kimi_coding_usages.json` (PII-scrubbed
capture of the live `/usages` body — window structure + real numbers preserved; `user.userId` redacted).
**Reference analyzers:** `optio-claudecode/account.py`, `optio-codex/account.py`, `optio-cursor/account.py`.

## 0. Safety confirmation (operator-requested)

- **Seed ALIVE:** YES. `GET https://api.kimi.com/coding/v1/usages` with the stored access token
  returned **HTTP 200** and a full usage body (see §3). The seed's Mongo status is `alive`.
- **READ-ONLY, no seed mutation:** confirmed. The capture performed **only HTTP GET** requests
  (`/usages` + four 404-probe identity paths). There was **NO Mongo write** (GridFS blob and the
  seed doc were read via `fs.get(...)` / `find_one(...)` only), **NO token refresh** (the rotating
  single-use `refresh_token` was never sent to `auth.kimi.com`), and **NO source edit / git commit**.
  The only files written were the fixture + this doc.
- **Token validity / expiry:** the stored access token is a **valid, unexpired** ES256 JWT.
  `exp = 1783585192` → **2026-07-09T08:19:52Z**; at capture (~08:14:39Z) it had **~313 s** of life
  left and the GET succeeded well inside that. NOTE: 313 s is *inside* kimi's refresh threshold
  (`max(300, 0.5·900) = 450 s`), i.e. the seed's next verify pass will legitimately rotate it — but
  **this capture did not**, as mandated. The token was used exactly as stored.

## TL;DR — corrected: kimicode exposes usage AND partial identity

- **Usage / limit windows: FULLY available read-only** via the seed's OAuth bearer —
  `GET https://api.kimi.com/coding/v1/usages` → a rolling account quota (`usage`) + a sub-window list
  (`limits[]`, here one `300 MINUTE` = 5 h window), each with absolute `limit`/`used`/`remaining` +
  a `resetTime`. So `AccountInfo.windows` is populated and `is_limited(...)` is meaningful. (This part
  of the prior research held up.)
- ⚠ **Identity is PARTIALLY exposed — the prior `no_seed` doc was wrong.** The live `/usages` body
  carries a `user` block: `user.userId` (an account id, **identical to the JWT `sub`**),
  `user.region`, and `user.membership.level` (a plan/tier, e.g. `"LEVEL_BASIC"`). So
  `AccountInfo.account_id` and `AccountInfo.plan` **CAN be populated** from the very same read-only
  GET. What is still absent: **name and email** (no field anywhere in the payload, and no dedicated
  identity endpoint — `/coding/v1/{auth,me,user,users/me}` all 404).
- **`AccountInfo.summary` is still `None`** — it requires `plan` AND `email`; `email` is unavailable,
  so the derived summary stays `None` even though `plan` is now known.

Consequence: kimicode ships **usage gating + account id + plan tier, but no human-readable summary**
(no email/name). Not the "no identity at all" of the earlier draft.

## 1. Credential layout (from the wrapper) — confirmed against the live seed

Source: `optio_kimicode/verify.py`, `seed_manifest.py`; confirmed by extracting the live blob.

- **Seed store:** Mongo `excavator.gm_kimicode_seeds` (prefix `gm`), GridFS blob. The blob is
  **age-encrypted** (magic `age-`), decrypted with `engine.credentials.decrypt_session_blob`
  (NOT plain gzip for this seed — the "Header is invalid → plain gzip" fallback was not needed here).
- **Seed-blob members (live):** `credentials/`, `config.toml`, `credentials/kimi-code.json` — exactly
  the `KIMI_SEED_MANIFEST` (creds dir + `config.toml` provider registration).
- **`kimi-code.json` shape (live):** flat snake_case dict with keys
  `access_token` / `refresh_token` / `expires_at` (unix SECONDS) / `expires_in` (900) / `scope`
  (`"kimi-code"`) / `token_type` (`"Bearer"`). Confirmed — matches `verify.py`.
- **Access-token field:** `creds["access_token"]` — the bearer for `analyze_account`.
  ⚠ It is **NOT opaque**: it is a decodable ES256 JWT. Header `{"alg":"ES256","kid":…,"typ":"JWT"}`;
  payload carries `sub`/`user_id` (= the account id), `client_id`, `scope` (`"kimi-code"`),
  `token_id`/`jti`, `iss` (`"kimi-auth"`), `exp`/`nbf`/`iat`, `type` (`"access"`). It carries **NO
  email/name/plan** claims. Because `/usages` returns the same `userId` (plus the plan tier) directly,
  the analyzer does **not** need to decode the JWT — prefer the `/usages` `user` block as the source.
- **Vendor OAuth (refresh):** `POST https://auth.kimi.com/api/oauth/token` (public client
  `17e5f671-d194-4dfb-9706-5516cb48c098`). **The analyzer must NOT refresh** — single-use rotating
  refresh token; a refresh without seed save-back strands the seed. Not exercised in this capture.

## 2. Vendor endpoints (reachability with the seed OAuth bearer) — live-verified

| Purpose | Method | URL | Result | Identity? | Usage? |
|---|---|---|---|---|---|
| **Usage / limits (+ partial identity)** | GET | `https://api.kimi.com/coding/v1/usages` | **200** ✅ | **partial** (`user.userId`, `user.membership.level`, `user.region`) | **YES** |
| Identity summary probe | GET | `https://api.kimi.com/coding/v1/auth` | **404** (`resource_not_found_error`) | — | — |
| Identity probe | GET | `https://api.kimi.com/coding/v1/me` | **404** | — | — |
| Identity probe | GET | `https://api.kimi.com/coding/v1/user` | **404** | — | — |
| Identity probe | GET | `https://api.kimi.com/coding/v1/users/me` | **404** | — | — |
| OAuth token (refresh) | POST | `https://auth.kimi.com/api/oauth/token` | not called | — | — (do NOT call; rotates single-use token) |

Auth header used: `Authorization: Bearer <access_token>`, `Accept: application/json`,
`User-Agent: optio-kimicode/account`. A plain UA authorized the GET fine — no `X-Msh-*` device
headers or `kimi_code_cli` UA were needed (confirms the prior "custom UA not required" note).
There is **no dedicated identity endpoint**; identity, such as it is, is bundled INTO `/usages`.

## 3. `/coding/v1/usages` — REAL live payload

Captured verbatim (then scrubbed for the fixture). ⚠ Differs from the earlier code-derived guess in
several load-bearing ways — the analyzer must be written against THIS shape:

```json
{
  "user": {
    "userId": "<opaque account id — == JWT sub>",
    "region": "REGION_OVERSEA",
    "membership": { "level": "LEVEL_BASIC" },
    "businessId": ""
  },
  "usage":  { "limit": "100", "used": "2", "remaining": "98",
              "resetTime": "2026-07-11T20:30:55.151098Z" },
  "limits": [
    { "window": { "duration": 300, "timeUnit": "TIME_UNIT_MINUTE" },
      "detail": { "limit": "100", "remaining": "100",
                  "resetTime": "2026-07-09T10:30:55.151098Z" } }
  ],
  "parallel":       { "limit": "10" },
  "totalQuota":     { "limit": "100", "remaining": "99" },
  "authentication": { "method": "METHOD_ACCESS_TOKEN", "scope": "FEATURE_CODING" },
  "subType":        "TYPE_PURCHASE"
}
```

Load-bearing differences vs the prior guess (analyzer must handle these):

- ⚠ **Counts are JSON STRINGS, not integers** (`"100"`, `"2"`). Parse with `int(...)` (tolerate
  numeric strings). `pct = 100 * used / limit`, guard `limit > 0`.
- ⚠ **`used` is only present on the top-level `usage`; the `limits[].detail` gives only
  `limit`+`remaining`** → derive `used = limit - remaining` there (here `100 - 100 = 0` → 0 %).
- ⚠ **Reset field is `resetTime`** (camelCase), an ISO-8601 string with **microsecond** precision
  (`.151098Z`). Parse with fractional-second tolerance. (No `resetAt`/`reset_at`/relative-seconds in
  the live body — the parser's other spellings are defensive only.)
- ⚠ **`window.timeUnit` is an ENUM string, prefixed `TIME_UNIT_`** (`TIME_UNIT_MINUTE`), not bare
  `MINUTE`. Strip the `TIME_UNIT_` prefix before mapping to a label. `duration:300` + `MINUTE`
  = 300 min = **5 h window**.
- **No `name`/`title` field on any window.** Labels must be synthesized: top-level `usage` →
  a default like `"Account quota"`; each `limits[]` entry → from its `window` (`300 MINUTE` → `"5h"`).
- **`parallel`, `totalQuota`, `authentication`, `subType`** are extra top-level blocks not in the
  prior guess. Not usage windows (parallel-request cap / dup quota / auth method / purchase type);
  keep them only in `raw`.
- **Per-model scoping: NONE** (account-wide quota). Every `UsageWindow.model = None`. (Held up.)

## 4. AccountInfo field mapping — CORRECTED against the live payload

`analyze_account(token: str)` — a bearer string in (mirror cursor's signature). One read-only GET
(`/usages`), fail-soft to `EMPTY` on any error/expiry.

| AccountInfo field | Source (live JSON path) | Value on this seed | Notes |
|---|---|---|---|
| `name` | — | `None` | ⚠ still no name field anywhere. |
| `email` | — | `None` | ⚠ still no email field anywhere; keeps `summary` `None`. |
| `plan` | `user.membership.level` | `"LEVEL_BASIC"` | ⚠ **now available** (prior doc said None). Humanize `LEVEL_BASIC` → `"Basic"` if desired. |
| `account_id` | `user.userId` | `<opaque id>` | ⚠ **now available** (prior doc said None). Equals the JWT `sub`; prefer the `/usages` field over decoding the JWT. |
| `windows` | `usage` (1) + each `limits[].detail` (n) | 2 % account quota; 0 % 5h | see §3: counts are strings, derive `used` from `limit-remaining` in `limits[]`, `resetTime` per row. |
| `raw` | `{"usage": <full /usages json>}` | — | keep the extra blocks (`parallel`/`totalQuota`/`authentication`/`subType`) here. |

`AccountInfo.summary` → **`None`** for kimicode (needs `plan` + `email`; `email` absent). The value of
the analyzer is the usage windows + `account_id`/`plan`, not a human summary string.

Window-building sketch (updated for strings + `TIME_UNIT_` prefix + `remaining` fallback):

```
def _int(x):  # tolerate "100" and 100
    try: return int(str(x).strip())
    except (TypeError, ValueError): return None

rows = []
if isinstance(j.get("usage"), dict):
    rows.append(("Account quota", j["usage"]))
for i, e in enumerate(j.get("limits") or []):
    d = e.get("detail") or {}
    w = e.get("window") or {}
    unit = str(w.get("timeUnit","")).removeprefix("TIME_UNIT_")   # MINUTE/HOUR/DAY
    label = synth_label(w.get("duration"), unit) or f"Limit #{i+1}"   # 300 MINUTE -> "5h"
    rows.append((label, d))

for label, r in rows:
    limit = _int(r.get("limit"))
    if not limit or limit <= 0: continue
    used = _int(r.get("used"))
    if used is None:
        rem = _int(r.get("remaining"));  used = (limit - rem) if rem is not None else None
    if used is None: continue
    pct = 100.0 * used / limit
    resets_at = parse_iso(r.get("resetTime"))     # microsecond precision, tz-aware
    windows.append(UsageWindow(label=label, pct=pct, resets_at=resets_at, model=None))
```

## 5. Usage / limits — exposed?

**YES — read-only, live-verified 200.** kimicode's distinguishing strength among the bearer-only
wrappers: real per-account quota utilization + reset windows, so `supports_usage_gating` can be
**True** and `is_limited(...)` is meaningful. On this seed: account quota 2/100 used (2 %, resets
2026-07-11), 5h window 0/100 (0 %, resets 2026-07-09 10:30). Two windows total.

## 6. Blockers / open items — updated

1. ✅ **Live capture done.** `capture_status = captured`; fixture written. Remaining code/docs-only
   assumptions are now nailed to the served shape (string counts, `resetTime` camelCase + µs,
   `TIME_UNIT_MINUTE` enum, `remaining`-only in `limits[]`).
2. ⚠ **Identity is partially available (corrected):** `account_id` (`user.userId`) and `plan`
   (`user.membership.level`) ARE reachable read-only via `/usages`; only **name/email** are absent, so
   `summary` stays `None`. There is NO dedicated identity endpoint (`/auth`,`/me`,`/user`,`/users/me`
   all 404) — identity rides inside `/usages`.
3. **pct is computed, not read** (absolute `limit`/`used`/`remaining`, as STRINGS) — divide, handle
   the `remaining` fallback and the `limit == 0` guard, and `int()`-coerce the strings.
4. **No token refresh** — single-use rotating refresh token; analyzer uses the stored token as-is;
   an expired token → `EMPTY` (fail-soft), never a refresh. (Not exercised here.)
5. **`parallel`/`totalQuota`/`authentication`/`subType`/`user.region`/`user.businessId`** are present
   but not account fields — park them in `raw` only.

## 7. Environment / method (reproducibility)

- **Capture env:** `EXCAVATOR_KEY_DIR=/home/csillag/excavator-trinkets`,
  `python=/home/csillag/deai/excavator/.venv/bin/python` (pymongo, gridfs, `engine.credentials`,
  optio wrappers). Mongo `mongodb://localhost:27017` db `excavator` coll `gm_kimicode_seeds`.
- **Steps (all read-only):** `gridfs.GridFS(db).get(blobId).read()` → `engine.credentials.
  decrypt_session_blob` (age) → `tarfile r:gz` extract `credentials/kimi-code.json` → use stored
  `access_token` → `GET /coding/v1/usages` (200) + four identity-endpoint probes (404). No Mongo
  write, no refresh, no source edit, no commit.
- **Vendor source of truth (unchanged):** `~/deai/kimi-code/packages/oauth/src/` — `managed-usage.ts`,
  `managed-kimi-code.ts`, `constants.ts`, `token-state.ts`.
