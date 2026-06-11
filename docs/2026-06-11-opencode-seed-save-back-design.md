# Opencode Seed Save-Back (Refresh + Verify)

This spec was written against the following baseline:

**Base revision:** `882285f2dc0c0a4700c06f7c663aba09f8938b2b` on branch `main` (as of 2026-06-11T11:55:48Z)

## Summary

`optio-opencode` seeds are **write-once snapshots** of a provider `auth.json`. They have **no token-refresh or write-back**, unlike `optio-claudecode` (which has `oauth.py` + `cred_watcher.py`). For OAuth providers with short-lived access tokens and **rotating refresh tokens** (notably xAI; likely OpenAI/Codex), this means a seed goes **stale after first use**: a seeded task refreshes the access token from the seed's refresh token, the provider rotates the refresh token, the new one lands only in the task's ephemeral copy, and the seed keeps the now-invalid old one. The next seeded task can't refresh → that provider is dead.

This feature makes opencode seeds durable with two independent surfaces, both of which **delegate the actual auth work to opencode itself** rather than re-implementing per-provider refresh:

1. **Watcher** — in-session live write-back. While a real task runs, opencode's own plugin `loader()` refreshes tokens; a watcher persists the changed `auth.json` back into the seed blob. Provider-agnostic.
2. **Verify/refresh** — a standalone, on-demand maintenance function the consuming app calls to confirm a seed is alive (and refresh it) **before** handing it to a task. It runs a throwaway `opencode run` probe and writes the refreshed `auth.json` back.

## Prior context (already shipped / done — not re-specced here)

- **Widget-proxy timeout fix** (`optio-api` 0.2.6): `replyOptions.timeout: 0` in the Fastify widget proxy, so held-open device-code OAuth callbacks survive the proxy. This unblocked interactive login end-to-end through the 3-host topology.
- **Fork loopback-strip** (`csillag/opencode` `make-web-embeddable-in-iframes`): removed the Class-C loopback OAuth methods (xAI SuperGrok, OpenAI Codex browser, DigitalOcean) so the connect dialog only offers topology-viable methods (device-code + api-key).

These established that interactive login works and produces seeds. This spec is the seed-durability follow-up.

## Evidence (from inspecting the captured demo seed, 2026-06-11)

One seed, 4 providers: `anthropic` (api-key, no expiry), `github-copilot` (oauth, `expires:0`, long-lived), `xai` (oauth, access expired **+5h34m** after capture, has refresh), `openai` (oauth, access **+~10 days**, has refresh). Default model recorded in `opencode.json` (`openai/gpt-5.4-mini`). Seed stored **unencrypted** in `optio-demo` (the demo sets no `session_blob_encrypt`). `optio-opencode` has **no** `oauth.py`/`cred_watcher.py`/`account.py`.

## Design

### Component 1 — `cred_watcher.py` (live write-back)

A close port of `optio_claudecode/cred_watcher.py`, simplified by opencode's multi-provider-agnostic nature.

- **Watches** `home/.local/share/opencode/auth.json` (vs claudecode's `.claude/.credentials.json`).
- **Change detection**: SHA-256 fingerprint of the file, polled on an interval (mirror claudecode's `CRED_WATCH_INTERVAL_S`).
- **On change**: call `optio_agents.seeds.refresh_seed(ctx, host, seed_id=…, manifest=OPENCODE_CRED_MANIFEST, suffix=OPENCODE_SEED_SUFFIX, encrypt=…, decrypt=…)` to re-capture `auth.json` into the seed's GridFS blob.
- **Provider-agnostic**: the watcher does **not** refresh anything itself. Opencode's plugin `loader()` refreshes a provider's token when that provider is used; the watcher only persists the resulting file. Multi-provider is therefore free.
- **`OPENCODE_CRED_MANIFEST`**: a credential-only manifest (just `.local/share/opencode/auth.json`), the write-back analog of the full `OPENCODE_SEED_MANIFEST`. Mirrors claudecode's `CLAUDE_CRED_MANIFEST`.
- **Lease handling**: if opencode seeds use the seeds-engine lease mechanism, renew it each loop and cancel the session on lease loss (mirror claudecode). *(Confirm whether opencode seeds use leases; if not, omit.)*

**Session wiring** (`session.py`, mirroring claudecode at capture / consume / resume):
- Capture the fingerprint **baseline** right after `merge_seed` (or after planting, when no seed).
- **Start** the watcher task after `opencode web` launch.
- At teardown: a **final `save_back_if_changed()` backstop** (catches refreshes that happened between the last poll and exit), then cancel the watcher.
- Runs in all three modes: capture (seed-setup), consume (seeded fresh), resume.

### Component 2 — `verify_and_refresh_seed(...)` (standalone, on-demand)

A standalone async function — same **reachability/role** as claudecode's `verify_and_refresh_seed` (a maintenance entry point the consuming app calls; **never** auto-run on task launch). Structurally different because it must run the binary, but it has **no engine/HookContext dependency** (see Extractions).

```python
async def verify_and_refresh_seed(
    store, seed_id, *,
    ssh: SSHConfig | None = None,        # per-call execution target (no global host)
    install_dir: str | None = None,
    session_blob_encrypt=None, session_blob_decrypt=None,
    opencode_executable: str = "opencode",
    probe: str = "<trivial prompt>",
) -> VerifyResult:                        # { alive: bool, ... per-seed status }
```

Flow:
1. Build a `Host` from `ssh` (or `LocalHost`) + a throwaway `taskdir`, via the shared host-builder helper.
2. `ensure_opencode_installed(host, download=<context-free downloader>, report_progress=None, install_dir=install_dir)` — same fork binary as real tasks.
3. Provision the workdir, apply `_isolation_env(host)`, `merge_seed(...)` to plant `auth.json` + `opencode.json`.
4. Run **`opencode run --format json "<probe>"`** against the seed's **default model** (from the planted `opencode.json`), headless one-shot, capture output + exit.
5. **Alive iff** the model produced an assistant response; **dead** on an auth/permission error event or nonzero exit.
6. Read the (refreshed) `auth.json` → `seeds.refresh_seed(...)` write-back to the seed.
7. Tear down the throwaway workdir; return status.

**Scope decision — default-provider verification.** One probe against the seed's default model verifies/refreshes exactly the provider a seed-pinned task will drive (the 80/20). Other providers in the seed are refreshed lazily by the watcher when used in a real session. All-provider pre-verification (N probes, one per provider's model) is **deferred** — more cost/latency, only warranted if a consumer needs it.

### The three extractions (decouple from the engine context)

The only thing tying the install path to a `HookContext` is three narrow seams. Extract them so verify needs no engine context:

1. **`ensure_opencode_installed`** → inject `(host, download, report_progress=None)` instead of `hook_ctx`. `download: Callable[[url, dest], Awaitable]`.
   - Session adapts: `host=hook_ctx._host, download=hook_ctx.download_file, report_progress=hook_ctx.report_progress`. Behavior unchanged.
   - Verify supplies a trivial context-free downloader and a no-op progress.
2. **Host-builder** → lift the existing `session.py` helper (`ssh_config + taskdir → LocalHost/RemoteHost`, already engine-free and test-monkeypatchable) to a reusable location shared by session and verify.
3. **Context-free downloader** — a ~10-line `download(url, dest)` (host-side `curl -L`/HTTP GET) for verify, vs the engine's child-task `download_file`.

These also leave the install code cleaner (engine-decoupled) regardless.

## Explicitly out of scope / not ported

- **`oauth.py`** (per-provider HTTP refresh re-implementation) and **`account.py`** (Anthropic profile summary) — both replaced by "let opencode do the auth work." opencode's `on_seed_saved` info is already the model, not an account summary.
- All-provider pre-verification (see scope decision).
- Seed **encryption wiring in Excavator** (the demo stores plaintext; production should ride `session_blob_encrypt/decrypt`) — a separate Excavator follow-up.

## Open items to confirm during implementation

- Exact `opencode run --format json` event shape for an **auth/permission failure** (to drive alive/dead detection) — one inspection of the run event stream.
- Whether opencode seeds use the seeds-engine **lease** mechanism (drives the watcher's lease handling).
- Exact name of the `session.py` host-builder helper (obscured in grep output) and the cleanest shared location.
- Final **probe** prompt text (trivial, minimal tokens, e.g. "reply OK").

## Testing

- **Unit**: fingerprint stability/change; `save_back_if_changed` calls `refresh_seed` only on real change; verify's alive/dead parsing against captured `--format json` fixtures (success + auth-error).
- **Integration** (uses `optio-demo`, MongoDB via Docker): `verify_and_refresh_seed` on a known-good seed returns `alive` and writes back a refreshed `auth.json`; a seed with a deliberately-broken token returns `dead`. Watcher: a seeded session that triggers a refresh results in an updated seed blob.

## Config surface

No new task-config fields required for the watcher (reuses existing `session_blob_encrypt/decrypt`, `seed_id`, `on_seed_saved`). `verify_and_refresh_seed` is a standalone function, parameterized per call (above), not via `OpencodeTaskConfig`.
