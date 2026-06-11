# Opencode Seed Save-Back (Refresh + Verify)

This spec was written against the following baseline:

**Base revision:** `882285f2dc0c0a4700c06f7c663aba09f8938b2b` on branch `main` (as of 2026-06-11T11:55:48Z)

Revised 2026-06-11 after design review (verify mechanics, gates, leases, scope).

## Summary

`optio-opencode` seeds are **write-once snapshots** of a provider `auth.json`. They have **no token-refresh or write-back**, unlike `optio-claudecode` (which has `oauth.py` + `cred_watcher.py`). For OAuth providers with short-lived access tokens and **rotating refresh tokens** (notably xAI; likewise OpenAI/Codex), this means a seed goes **stale after first use**: a seeded task refreshes the access token from the seed's refresh token, the provider rotates the refresh token, the new one lands only in the task's ephemeral copy, and the seed keeps the now-invalid old one. The next seeded task can't refresh → that provider is dead. (Rotation + write-back confirmed in the opencode fork source: `xai.ts` / `openai/codex.ts` plugins refresh via `grant_type=refresh_token` and persist the rotated pair through `auth.set()` → `auth.json`.)

This feature makes opencode seeds durable with two independent surfaces, both of which **delegate the actual auth work to opencode itself** rather than re-implementing per-provider refresh:

1. **Watcher** — in-session live write-back. While a real task runs, opencode's own plugin `loader()` refreshes tokens; a watcher persists the changed `auth.json` back into the seed blob. Provider-agnostic.
2. **Verify/refresh** — a standalone, on-demand maintenance function the consuming app calls to confirm a seed is alive (and refresh it) **before** handing it to a task. It runs a throwaway `opencode run` probe and writes the refreshed `auth.json` back.

Because rotation makes refresh tokens **single-use**, two concurrent sessions on one seed strand each other; the seeds-engine **lease** layer (already generic in `optio_agents.seeds`) is therefore wired in from the start, mirroring claudecode.

## Prior context (already shipped / done — not re-specced here)

- **Widget-proxy timeout fix** (`optio-api` 0.2.6): `replyOptions.timeout: 0` in the Fastify widget proxy, so held-open device-code OAuth callbacks survive the proxy. This unblocked interactive login end-to-end through the 3-host topology.
- **Fork loopback-strip** (`csillag/opencode` `make-web-embeddable-in-iframes`): removed the Class-C loopback OAuth methods (xAI SuperGrok, OpenAI Codex browser, DigitalOcean) so the connect dialog only offers topology-viable methods (device-code + api-key).

These established that interactive login works and produces seeds. This spec is the seed-durability follow-up.

## Evidence (from inspecting the captured demo seed, 2026-06-11)

One seed, two members. `auth.json`, 4 providers: `anthropic` (api-key, no expiry), `github-copilot` (oauth, `expires:0`, long-lived), `xai` (oauth, access expired **+5h34m** after capture, has refresh), `openai` (oauth, access **+~10 days**, has refresh + `accountId`). `opencode.json` whose **sole content** is the default model (`{"model": "openai/gpt-5.4-mini"}`) — without a model value the seed's auth cannot be utilized (no default for a consuming task, nothing for verify to probe). Seed stored **unencrypted** in `optio-demo` (the demo sets no `session_blob_encrypt`). `optio-opencode` has **no** `oauth.py`/`cred_watcher.py`/`account.py`.

## Design

### Considered alternative: host-free HTTP refresh (rejected)

The claudecode template's `verify_and_refresh_seed` never runs the binary: it refreshes via direct HTTP against Anthropic's token endpoint (`oauth.py`) and writes the one member back with `seeds.overwrite_seed_member`. The opencode analog (option A) would port each rotating-oauth provider's refresh call (today: xAI, OpenAI — `CLIENT_ID` + token URL + request shape, ~15 lines each; api-key and non-rotating providers need nothing). Cheap, host-free, and would make all-provider verification cheap too.

**Chosen instead: run the binary (option B).** Rationale:

- **Zero per-provider auth code to maintain.** Option A's refresh copies must track opencode's upstream auth changes forever; a drift-detection test against the fork source (`../opencode`) could flag breakage but not fix it.
- **Auto-tracks upstream.** If opencode's own auth logic changes, the probe exercises the new logic for free.
- **End-to-end signal.** A successful completion proves the seed is *usable* (auth + account standing + model access), not merely that a token can be minted. Option A can mark seeds alive that die on first real use.
- **Probe cost is negligible.** The probe is a one-line completion against a small model; any real task it gates burns orders of magnitude more. Binary install is cached on the worker (one-time).

Consequence: all-provider pre-verification is expensive under B (N completions, one per provider) — hence the default-provider scope decision below. Under A it would have been cheap; this trade is accepted knowingly.

### Component 1 — `cred_watcher.py` (live write-back)

A close port of `optio_claudecode/cred_watcher.py`, simplified by opencode's multi-provider-agnostic nature.

- **Watches** `home/.local/share/opencode/auth.json` (vs claudecode's `.claude/.credentials.json`).
- **Change detection**: SHA-256 fingerprint of the file, polled on an interval (mirror claudecode's `CRED_WATCH_INTERVAL_S`).
- **Validity gates** (the multi-provider analog of claudecode's refresh-token gate; claudecode keys on `claudeAiOauth.refreshToken`, opencode has no single such field). Two gates, different strictness:
  - **Save-back gate** (fingerprint returns `None` → skip): `auth.json` must be parseable JSON with **≥1 provider entry**. Guards against persisting a half-written or logged-out file. Scoped to the file being saved — deliberately does *not* check the model field, because save-back only replaces `auth.json` (the seed's `opencode.json` is untouched), and blocking a save-back over an unrelated field would drop a rotated refresh token (the very bug this spec fixes).
  - **Capture gate** (skip `capture_seed` + `on_seed_saved`): save-back gate **plus** a non-empty `model` in the live `opencode.json`. A model-less seed is unusable (see Evidence); today's session code writes the model config only when `seed_model` is resolved, so a model-less capture is currently possible — this gate closes that hole.
- **On change**: call `optio_agents.seeds.refresh_seed(ctx, host, seed_id=…, manifest=OPENCODE_CRED_MANIFEST, suffix=OPENCODE_SEED_SUFFIX, encrypt=…, decrypt=…)` to re-capture `auth.json` into the seed's GridFS blob. (The watcher runs in-session and has the engine `ctx`; contrast with verify's host-free write-back below.)
- **Provider-agnostic**: the watcher does **not** refresh anything itself. Opencode's plugin `loader()` refreshes a provider's token when that provider is used; the watcher only persists the resulting file. Multi-provider is therefore free.
- **`OPENCODE_CRED_MANIFEST`**: a credential-only manifest (just `.local/share/opencode/auth.json`), the write-back analog of the full `OPENCODE_SEED_MANIFEST`. Mirrors claudecode's `CLAUDE_CRED_MANIFEST`.
- **Leases — full claudecode mirror.** Rotating refresh tokens are single-use: two live sessions on one seed strand each other, and the stranded one's save-back would clobber the seed with a stale pair. So:
  - `OpencodeTaskConfig.seed_id` widens to `str | SeedProvider | None` (claudecode's callable form). When callable, the session resolves it (`await config.seed_id(ctx.process_id)`) — the consuming app's callable does the pool `acquire` inside — and sets `lease_holder = ctx.process_id`.
  - The watcher renews the lease each tick (`seeds.renew_lease`, CAS on holder); on lease loss it sets `ctx.cancellation_flag` and exits — continuing would mean a token-rotation collision.
  - Teardown releases the lease (holder-guarded `seeds.release`) in `finally`; the TTL reclaims if release is missed.
  - The lease machinery itself (`acquire`/`renew_lease`/`release`/`mark_seed_status`/`assign_to_pool`/`list_pool`) already lives provider-agnostic in `optio_agents.seeds` — no port needed. Pool policy (assignment, eviction) is the consuming app's.

**Session wiring** (`session.py`, mirroring claudecode at capture / consume / resume):
- Capture the fingerprint **baseline** right after `merge_seed` (or after planting, when no seed).
- **Start** the watcher task after `opencode web` launch.
- At teardown: a **final `save_back_if_changed()` backstop**, then cancel the watcher, then release the lease. The backstop is **load-bearing, not defensive**: opencode's own auth write-back is best-effort (`auth.set(...).catch(() => {})` — xai plugin) and the provider has already consumed the old refresh token by then; a rotation in the last poll window is saved *only* here.
- Runs in all three modes: capture (seed-setup), consume (seeded fresh), resume.

### Component 2 — `verify_and_refresh_seed(...)` (standalone, on-demand)

A standalone async function — same **reachability/role** as claudecode's `verify_and_refresh_seed` (a maintenance entry point the consuming app calls; **never** auto-run on task launch). It must run the binary (see Considered alternative), but like claudecode's it has **no engine/HookContext dependency** (see Extractions) and the same db-first signature:

```python
async def verify_and_refresh_seed(
    db, *,
    prefix: str,
    suffix: str = OPENCODE_SEED_SUFFIX,
    seed_id: str,
    ssh: SSHConfig | None = None,        # per-call execution target (no global host)
    install_dir: str | None = None,
    encrypt=None, decrypt=None,          # session-blob crypto, as elsewhere
) -> VerifyResult:                       # { alive: bool, model: str | None }
```

**Lease contract** (docstring, same as now documented on claudecode's): call **only** on a free seed, or one whose lease the caller holds. The probe rotates the single-use refresh token; verifying a seed a live session holds strands that session. The function does not acquire or check leases — the caller owns lease discipline (Excavator already follows this pattern with claudecode).

Flow:
1. Build a `Host` from `ssh` (or `LocalHost`) + a throwaway `taskdir`, via the shared host-builder helper.
2. `ensure_opencode_installed(host, download=<context-free downloader>, report_progress=None, install_dir=install_dir)` — same fork binary as real tasks, from the shared worker cache (see invariant under Extractions).
3. Provision the workdir, apply `_isolation_env(host)`, `merge_seed(...)` to plant `auth.json` + `opencode.json`.
4. Run the **probe**: `opencode run "<PROBE>"`, headless one-shot, plain output (no `--format json` — not needed, see verdict), against the seed's default provider. Model: the planted `opencode.json`'s `small_model` if set, else `model` — must stay on the **default provider**, since that is whose token is being verified. Capture stdout + exit code.
5. **Verdict — challenge-answer, stdout only.** `PROBE = "What is the capital of France?"`; `alive = re.search(r"paris", stdout, re.I) is not None`. The answer token does not appear in the prompt, so no error path that echoes/quotes the prompt can false-positive, and "paris" is improbable in error noise (unlike digits, which collide with error codes and line numbers). Exit code and stderr carry **zero bits of verdict** — a present answer proves the full chain worked regardless of exit code (requiring exit 0 too would only add a false-dead path); they are logged as diagnostics when dead (distinguish died-before-output from wrong/empty output).
6. Read the (refreshed) `auth.json` from the host → **`seeds.overwrite_seed_member(db, prefix=…, suffix=…, seed_id=…, member_path=".local/share/opencode/auth.json", content=…, encrypt=…, decrypt=…)`**. Host-free, ctx-free, crash-safe single-member write-back — exactly how claudecode's verify writes back (its watcher uses `refresh_seed(ctx, host, …)` because it runs in-session; verify has no ctx, and `overwrite_seed_member` already exists for this).
7. **Persist the verdict** so the pool can act on it: `seeds.declare_metadata(db, …, metadata={"verify": {"alive": …, "checkedAt": …, "probedModel": …}})` and `seeds.mark_seed_status(db, …, status="alive"/"dead")` — dead seeds are never handed out by `acquire`. (A return value alone is invisible to the pool.)
8. Tear down the throwaway workdir; return.

**Launch seam.** The probe's argv is built to accept an optional wrap prefix (a list prepended to the command), unused for now. This is the hook for claustrum fs-isolation parity (separate spec) so verify won't need re-touching.

**Scope decision — default-provider verification.** One probe against the seed's default model verifies/refreshes exactly the provider a seed-pinned task will drive (the 80/20). Other providers in the seed are refreshed lazily by the watcher when used in a real session. All-provider pre-verification (N probes, one per provider's model) is **deferred** — expensive under the chosen run-the-binary approach (see Considered alternative), only warranted if a consumer needs it.

### The three extractions (decouple from the engine context)

The only thing tying the install path to a `HookContext` is three narrow seams. Extract them so verify needs no engine context:

1. **`ensure_opencode_installed`** → inject `(host, download, report_progress=None)` instead of `hook_ctx`. `download: Callable[[url, dest], Awaitable]`.
   - Session adapts: `host=hook_ctx._host, download=hook_ctx.download_file, report_progress=hook_ctx.report_progress`. Behavior unchanged.
   - Verify supplies a trivial context-free downloader and a no-op progress.
   - **Invariant to preserve:** install-dir resolution runs against the host's **real** env, never under `_isolation_env` (the current code already does this — `_resolve_install_dir` docstring). If the throwaway-workdir isolation env leaked into resolution, `XDG_CACHE_HOME` would point inside the workdir: the binary would re-download per probe and be deleted at teardown. The shared worker cache (`${OPENCODE_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}}/optio-opencode/bin`) must stay outside every workdir.
2. **Host-builder** → lift the existing `session.py` helper (`ssh_config + taskdir → LocalHost/RemoteHost`, already engine-free and test-monkeypatchable) to a reusable location shared by session and verify.
3. **Context-free downloader** — a ~10-line `download(url, dest)` (host-side `curl -L`/HTTP GET) for verify, vs the engine's child-task `download_file`.

These also leave the install code cleaner (engine-decoupled) regardless. The seed-blob write-back needs **no** extraction: `seeds.overwrite_seed_member` is already host- and ctx-free.

## Explicitly out of scope / not ported

- **`oauth.py`** (per-provider HTTP refresh re-implementation) and **`account.py`** (Anthropic profile summary) — both replaced by "let opencode do the auth work" (see Considered alternative). opencode's `on_seed_saved` info is already the model, not an account summary.
- All-provider pre-verification (see scope decision).
- Template features knowingly omitted (no opencode analog yet): `seed_signature` structural-divergence check, `usage_limited` rate-limit gating, account summaries.
- Seed **encryption wiring in Excavator** (the demo stores plaintext; production should ride `session_blob_encrypt/decrypt`) — a separate Excavator follow-up.
- **Claustrum fs-isolation parity** (`fs_isolation`, `ensure_claustrum_installed`, allowlist, launch wrap) — separate spec; this spec only leaves the wrap-prefix seam in verify's probe launch.

## Open items to confirm during implementation

- Exact name of the `session.py` host-builder helper (obscured in grep output) and the cleanest shared location.

## Testing

- **Unit**: fingerprint stability/change; the two validity gates (save-back gate: unparseable / empty-provider files skipped; capture gate: additionally skips on missing `model`); `save_back_if_changed` calls `refresh_seed` only on real change; verify's verdict (`paris` word-match, case-insensitive) against captured stdout fixtures (success + auth-failure runs — note exit code must not affect the verdict); verify's write-back goes through `overwrite_seed_member`; watcher lease: renew per tick, lease loss → cancellation flag set.
- **Integration** (uses `optio-demo`, MongoDB via Docker): `verify_and_refresh_seed` on a known-good seed returns `alive`, writes back a refreshed `auth.json`, and stamps verdict metadata/status; a seed with a deliberately-broken token returns `dead` and is marked dead. Watcher: a seeded session that triggers a refresh results in an updated seed blob.

## Config surface

`OpencodeTaskConfig.seed_id` widens from `str | None` to `str | SeedProvider | None` (callable form for pool acquire, mirroring claudecode). No other task-config changes (watcher reuses existing `session_blob_encrypt/decrypt`, `on_seed_saved`). `verify_and_refresh_seed` is a standalone function, parameterized per call (above), not via `OpencodeTaskConfig`.
