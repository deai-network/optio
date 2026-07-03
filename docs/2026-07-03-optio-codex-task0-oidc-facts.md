# Plan F — Task 0 pinned facts (OpenAI OIDC + auth.json + orphan-risk verdict)

Investigation output for `docs/2026-07-03-optio-codex-plan-f-guide-delta.md` Task 0.
Host-free curl probe + `codex` 0.142.5 binary-string inspection + local grep.
Consumed verbatim by Task 3 (orphan verdict) and Task 4 (verify.py docstring + endpoint).
Date pinned: 2026-07-03. Re-verify if the codex pin moves off 0.142.5.

## Baselines (compare every later task against these)

- Python: `188 passed, 4 skipped` (skips: `test_real_codex_session.py` + the 3
  `test_sandbox_enforce.py` cases). `.venv/bin/python -m pytest packages/optio-codex/tests/ -q`.
- conversation-ui: Vitest `135 passed (16 files)`; `tsc --noEmit` clean.

## OpenAI OIDC (host-free curl, discovery reachable + valid JSON)

`GET https://auth.openai.com/.well-known/openid-configuration`:

| field | value |
|---|---|
| `issuer` | `https://auth.openai.com` |
| `authorization_endpoint` | `https://auth.openai.com/api/accounts/authorize` |
| `token_endpoint` (discovery) | `https://auth.openai.com/api/accounts/oauth/token` |
| `userinfo_endpoint` | `https://auth.openai.com/api/accounts/oauth/userinfo` |

## ⚠️ Endpoint divergence — READ BEFORE IMPLEMENTING TASK 4

The codex binary does **not** use the discovery `token_endpoint` for its ChatGPT
refresh. `login/src/auth/manager.rs` (binary 0.142.5 strings) hardcodes:

- refresh URL: **`https://auth.openai.com/oauth/token`** (env override `CODEX_REFRESH_TOKEN_URL_OVERRIDE`)
- revoke URL:  `https://auth.openai.com/oauth/revoke` (env override `CODEX_REVOKE_TOKEN_URL_OVERRIDE`)

So the ChatGPT `refresh_token` grant must POST to `https://auth.openai.com/oauth/token`
— the discovery `token_endpoint` (`…/api/accounts/oauth/token`) is a *different*
OAuth surface (the account-management / rmcp-MCP flow, per the rmcp crate paths in
the binary) and is **not** what codex uses to rotate its own credential.

Consequence for Task 4: the plan's `_DISCO` test fixture already uses
`https://auth.openai.com/oauth/token` (correct), but the plan's production path
"`_discover_sync(issuer)` → use `disco['token_endpoint']`" would pick the WRONG
`…/api/accounts/oauth/token`. Recommendation: refresh against the codex-hardcoded
`https://auth.openai.com/oauth/token` (honoring `CODEX_REFRESH_TOKEN_URL_OVERRIDE`
if set); if discovery is still used, treat a discovery miss as the probe-fallback
trigger only, not as the source of the refresh URL. Fail-closed semantics unchanged
(4xx → dead; transport/discovery error → inconclusive).

## client_id

`app_EMoamEEZ73f0CkXaXp7hrann` — **confirmed present** in the codex 0.142.5 binary
strings (no client secret; public CLI client).

## auth.json shape (confirmed against a real authed `~/.codex/auth.json`)

- top-level keys: `OPENAI_API_KEY`, `auth_mode`, `last_refresh`, `tokens`
  — NOTE `auth_mode` is present (the plan's shape omitted it). Task 4 preserves it
  automatically since it only mutates `tokens` + `last_refresh` and rewrites the dict.
- `tokens` keys: `access_token`, `account_id`, `id_token`, `refresh_token`
- `last_refresh`: RFC3339 with **nanoseconds** + `Z`, e.g. `2026-07-02T00:11:54.339893366Z`
  — the plan's `_parse_last_refresh` (nanosecond→microsecond regex + `Z`→`+00:00`) covers this.
- `auth_mode` values seen in binary: `personal_access_token`, `bedrock_api_key`, `agent_identity`.
- **Single-use refresh confirmed:** binary error strings
  "refresh token was already used", "…has expired", "…was revoked" (all → invalid_grant /
  4xx → mark dead, fail-closed) — corroborates Gap 3's rotating-token rationale.

## Gap-3 orphan-risk verdict (grok `fc1e5ef` is N/A for codex)

- `grep -rn 'setsid|TIOCSCTTY|/dev/tty|controlling.tty' packages/optio-codex/src/optio_codex/` → **NONE**.
- Conversation launch (`session.py:305-318`): `argv = [codex_path, "app-server", …]`;
  `cmd = " ".join(shlex.quote(a) for a in argv)`; `host.launch_subprocess(cmd, …)`.
  No `exec`/`setsid`/tty wrapper — the launched pid **is** `codex app-server` in the
  launched process group, so `killpg` reaches it.
- Verdict: only grok `3f604c7` (seeded graceful teardown) ports as Gap 3; `fc1e5ef`
  (tty-wrapper `setsid`-escapes-`killpg`) has no codex analogue. Recorded here for the
  Task-3 commit body.
