# opencode account meta-analyzer — design

**Date:** 2026-07-09
**Status:** design (approved in discussion), pending implementation plan
**Depends on:** the account-analysis frame (`docs/2026-07-09-account-analysis-design.md`) — `AccountInfo`, `is_limited`, per-engine `analyze_account`, `metadata.account`.

## Problem

opencode has **no native account**. It is a model-selection TUI over third-party
providers: a seed's `auth.json` holds credentials for **whichever provider(s)**
the operator logged into (openai, anthropic, xai, openrouter, …). So an opencode
seed's "account" is really **one account per configured provider**, living at that
provider's API. Unlike the other six wrappers (one vendor, one account), opencode
needs a **meta-analyzer**: dispatch per provider, aggregate.

Two consequences settled in discussion:
1. **Multiple accounts per seed** — must list + aggregate all of them (not one).
2. **Reuse the vendor handlers we already built** (claudecode/codex/grok), don't
   reimplement.

## opencode credential shape

`<workdir>/home/.local/share/opencode/auth.json` = a dict keyed by `providerID`,
each value one of (opencode `auth/index.ts`):

- `{"type":"oauth", "access", "refresh", "expires", "accountId"?, "enterpriseUrl"?}`
- `{"type":"api", "key", "metadata"?}`
- `{"type":"wellknown", "key", "token"}`

The provider id is the models.dev id (`openai`, `anthropic`, `xai`, `openrouter`,
`deepseek`, `github-copilot`, … 152 total).

## Design decision 1 — the seam goes plural

`AccountInfo` is singular; today every engine's `analyze_account -> AccountInfo`
and `metadata.account` (singular) assume one account per seed. opencode breaks
that. Generalize:

- **`optio_agents.account`**: add nothing to `AccountInfo`; instead the per-engine
  entry point becomes **`analyze_accounts(creds) -> list[AccountInfo]`**. The six
  single-account engines return a **1-element list** (`[info]` or `[]` when
  nothing resolves); opencode returns **N**.
- **Metadata**: stamp **`metadata.accounts`** (a list of `AccountInfo.to_dict()`)
  instead of `metadata.account`. The six engines stamp a 1-element list.
- **`verify_and_refresh_seed`** returns `{"alive": bool, "accounts": list[AccountInfo]}`
  (was `{"alive", "account"}`).
- **Excavator** already deduplicates an `accounts` list in `handle_pool_stats`
  (across seeds). Change it to **flatten `metadata.accounts` from every seed**
  (was: read one `metadata.account` per seed). The contract/frontend `accounts[]`
  shape is unchanged (still `{account_id, summary, resets_at}`); only the source
  is now a per-seed list. gimme's `is_limited` gate: a seed is limited iff **every**
  usable account it carries is limited for the required models (opencode usability
  = "at least one provider account can serve"); detail in the plan.

This is a mechanical refactor of the 6 shipped engines (wrap the single result in
a list) + the excavator rollup + the frame types. It is the "necessary
refactoring" the reuse requires.

> **Migration note:** existing stamped `metadata.account` (singular) becomes stale.
> A reader fallback (`metadata.accounts` ?? `[metadata.account]`) avoids a hard
> break until pools are re-verified; the plan includes it.

## Design decision 2 — reuse via extracted map helpers

Each existing analyzer already separates *fetch-vendor-endpoints → AccountInfo*
from *extract-creds-from-its-own-seed*. Extract the **map step** into a reusable,
creds-form-agnostic helper per vendor, callable by opencode with opencode's creds:

- `optio_claudecode.account`: `account_from_oauth_token(access_token) -> AccountInfo`
- `optio_codex.account`: `account_from_openai(access_token, account_id) -> AccountInfo`
  (identity via `GET /backend-api/me` — opencode's openai-oauth stores **no
  `id_token`**, unlike codex, so we cannot decode identity offline; usage via
  `/backend-api/wham/usage`)
- `optio_grok.account`: `account_from_xai(access_token) -> AccountInfo`

opencode's provider handlers call these. New handlers (copilot, openrouter,
deepseek, and the api-key branches) live in `optio_opencode/providers/`.

## Design decision 3 — provider roster + placeholder

**Roster (analyzed handlers):** `anthropic`, `openai`, `xai`, `github-copilot`,
`openrouter`, `deepseek`. Any **other** provider (the ~146 remaining) is NOT
skipped — it yields a **placeholder** `AccountInfo`:

```
AccountInfo(plan=None, account_id=<accountId or None>,
            raw={"provider": <id>, "unanalyzed": True},
            name=None, email=None, windows=())
# summary property special-cases this → "Unknown account · <provider>"
```

So every configured provider shows up in the pool popover — analyzed ones with
their real identity/usage, unknown ones as "Unknown account · future-ai" — nothing
silently disappears.

### Handler matrix (auth type × provider)

| Provider | `oauth` handler | `api` handler | source of truth |
|---|---|---|---|
| anthropic | ✅ reuse claudecode map (profile+usage) | best-effort (raw keys expose ~no account) → placeholder | claudecode |
| openai | ✅ codex map via `/backend-api/me` + `wham/usage` | ~ org/usage (admin-key gated) → placeholder if unreachable | codex |
| xai | ✅ grok map (userinfo+subscriptions) | ✅ `GET api.x.ai/v1/api-key` (user/team id) | grok |
| github-copilot | ✅ **new** — GitHub `/user` + copilot quota *(endpoints: research)* | — | new |
| openrouter | — | ✅ **new** — `GET /api/v1/key` (usage,limit,label) + `/api/v1/credits` | new |
| deepseek | — | ✅ **new** — `GET /user/balance` (credit balance) | new |
| *any other* | placeholder | placeholder | — |

`wellknown` auth type → placeholder (niche).

## Architecture

```
optio_opencode/account.py
  async def analyze_accounts(auth: dict) -> list[AccountInfo]:
      out = []
      for provider_id, entry in auth.items():
          handler = _REGISTRY.get(provider_id)            # provider dispatch
          if handler is None:
              out.append(_placeholder(provider_id, entry))
              continue
          info = await handler(entry)                      # per-provider, per-auth
          out.append(info if info is not None else _placeholder(provider_id, entry))
      return out
optio_opencode/providers/
  anthropic.py  openai.py  xai.py  copilot.py  openrouter.py  deepseek.py
```

Each provider handler: `async def handle(entry: dict) -> AccountInfo | None`,
dispatches on `entry["type"]` (oauth/api), extracts the token/key, calls the
reused vendor map helper (or its own for copilot/openrouter/deepseek), fail-soft
(→ placeholder). Reuses the frame's `AccountInfo`/`UsageWindow`.

- **Wire into `verify_and_refresh_seed`** (opencode): after liveness, read the
  seed's `auth.json`, call `analyze_accounts`, stamp `metadata.accounts`.
- **Capture path** (`session.py`): same, from the live workdir `auth.json`.
- **Dispatcher** (`optio-agents-all`): the plural `analyze_accounts(agent_type, creds)`.
- **Excavator**: flip opencode `supports_accounts=True` (usage gating stays engine-
  by-provider — see plan); the accounts rollup flattens `metadata.accounts`.

## Error handling

Fail-soft throughout: a provider handler never raises (→ placeholder for that
provider); one bad provider never drops the others; `analyze_accounts` returns
`[]` only for empty/unreadable auth. Never blocks capture or launch.

## Testing

- Per-provider handler unit tests over **real captured fixtures** (one opencode
  seed carrying openai-oauth + xai + an api-key at minimum; capture read-only per
  the seed-safety rule) — assert each maps to the right `AccountInfo`.
- `analyze_accounts` aggregation test (multi-provider auth → N accounts, unknown
  provider → placeholder).
- Plural-seam regression: the 6 shipped engines still produce a 1-element
  `metadata.accounts`; excavator rollup flattens across seeds; `metadata.account`
  fallback works pre-re-verify.
- Fail-soft: a raising handler → placeholder, others unaffected.

## Open research items (resolve during the plan, against a live opencode seed)

1. **github-copilot** account + quota endpoints (GitHub `/user`, copilot usage) —
   unverified.
2. **openrouter** `/api/v1/key` + `/credits` response shape → `AccountInfo` mapping
   (confirm fields).
3. **deepseek** `/user/balance` shape.
4. **openai api-key** — whether any account/usage endpoint is reachable with a
   raw key (else placeholder).
5. Confirm opencode's **openai-oauth** identity path (`/backend-api/me`) works with
   opencode's stored `{access, accountId}` (no id_token) — validate live.

## Plan shape

1. **Frame plural refactor** (one unit): `analyze_accounts`/`metadata.accounts`,
   verify return `{alive, accounts}`, 6 engines wrapped to 1-element lists,
   excavator rollup flatten + `metadata.account` fallback, extract the 3 reusable
   vendor map helpers.
2. **opencode meta-analyzer core**: registry + dispatch + placeholder + the 3
   reuse handlers (anthropic/openai/xai), wire verify + capture + dispatcher +
   excavator flag.
3. **New handlers** (research-gated, one each): openrouter, deepseek, github-copilot.
4. Live-verify against a real opencode seed.
