# Blob-crypto mixin + seed_blob uniformity — implementation plan

**Goal:** main commit `f5da73ab` added a `seed_blob_encrypt/decrypt` crypto channel
(distinct from `session_blob_*`, pool-scoped vs ds-scoped keys) to claudecode +
opencode only. Make it uniform across all 7 engines — and instead of replicating
the fields + paired-validation a 7th time, hoist them into a shared
`BlobCryptoConfigMixin` in `optio_agents` (precedent: `ClaustrumConfigMixin`).
Bundle three related findings: cursor cli-config pre-seed clobber, opencode
resume config gap, claudecode AGENTS.md drift.

**Branch:** `csillag/blob-crypto-uniformity` (off local main `8d21b70e`).

**Verification: deferred to the final phase** — task agents write code + tests but
do NOT run suites; the final phase runs `make test` (needs the Docker Mongo on
:27017) and fixes fallout.

**Commit discipline:** each task commits ONLY its own listed paths
(`git add <explicit paths>`, never `-A`). No Co-Authored-By lines.

---

## Task 1 — optio-agents: `BlobCryptoConfigMixin` (SEQUENTIAL, FIRST)

Files: `packages/optio-agents/src/optio_agents/config_types.py`,
`packages/optio-agents/src/optio_agents/__init__.py`,
`packages/optio-agents/tests/test_blob_crypto.py` (new),
`packages/optio-agents/AGENTS.md`.

- Add `@dataclass(frozen=True) class BlobCryptoConfigMixin` next to
  `ClaustrumConfigMixin`, same doc style. Fields (all
  `Callable[[bytes], bytes] | None = None`):
  `session_blob_encrypt`, `session_blob_decrypt`,
  `seed_blob_encrypt`, `seed_blob_decrypt`.
  Docstring: session pair = per-process snapshot (ds-scoped key); seed pair =
  shared pool-account seeds (pool-scoped key); seed falls back to session when
  unset so single-key callers are unaffected.
- Accessor properties `seed_encrypt` / `seed_decrypt` returning
  `self.seed_blob_encrypt or self.session_blob_encrypt` (resp. decrypt) — the
  ONLY place the fallback rule lives.
- `_validate_blob_crypto(self)`: raise `ValueError` when either pair is
  asymmetric (one of encrypt/decrypt set without the other), message prefixed
  with `type(self).__name__` (mirrors existing per-engine messages). Engines
  call it from `__post_init__` like `_validate_claustrum`.
- Export the mixin from `optio_agents.__init__`.
- Tests: asymmetric session pair raises; asymmetric seed pair raises; both-set
  ok; fallback accessors return seed fns when set, session fns when seed unset,
  None when neither.
- AGENTS.md: document the mixin under the shared config section.

## Tasks 2–8 — engines (PARALLEL, after Task 1): claudecode, opencode, codex, cursor, grok, kimicode, antigravity

Common shape per engine (package-local paths only):

- `types.py`: add `BlobCryptoConfigMixin` to the config dataclass bases; DELETE
  the local `session_blob_encrypt/decrypt` (and, claudecode/opencode,
  `seed_blob_encrypt/decrypt`) field declarations and the local paired-validation
  block; call `self._validate_blob_crypto()` from `__post_init__`.
  Preserve field semantics/docs by pointing at the mixin.
- `session.py`: every SEED op call site passes `config.seed_encrypt` /
  `config.seed_decrypt` (accessors) — `merge_seed` (decrypt),
  `run_credential_watcher` (encrypt+decrypt; cursor and kimicode each have 2
  sites), `cred_watcher.save_back_if_changed` (encrypt+decrypt),
  `capture_seed` (encrypt). Kimicode: also the crypto args threaded through
  `_merge_seed_with_refresh` → `verify.verify_and_refresh_seed`. Snapshot
  (session-blob) sites keep `config.session_blob_*` verbatim.
  claudecode/opencode: replace their `config.seed_blob_X or
  config.session_blob_X` expressions with the accessors.
- Package `AGENTS.md`: document the seed_blob channel + mixin inheritance.
- Tests per engine: extend the existing verify/seed test module with a routing
  test — config with distinct seed_blob vs session_blob callables → seed ops
  use the seed pair, snapshot ops the session pair (mock `_seeds.merge_seed` /
  `capture_seed` and assert the callable identity). Keep it lightweight.

Engine-specific extras, same task/commit set:

- **claudecode (T2):** fix `packages/optio-claudecode/AGENTS.md:49-50` drift —
  `claude_config` is no longer "JSON-encoded to home/.claude/settings.json"
  pre-seed; it is a post-seed read-modify-write deep-merge
  (`apply_claude_settings`, caller wins per key, seed's other keys preserved,
  all paths incl. resume).
- **cursor (T5):** fix the pre-seed clobber. `session.py:367-371` writes
  `home/.cursor/cli-config.json` BEFORE `merge_seed` (line 404) as a whole-file
  write; the seed manifest includes the same file and seed extraction is
  overlay-overwrite → seeded fresh start loses the caller's
  allowed/disallowed_tools. Fix = mirror claudecode `5b2b781c`: move the write
  AFTER `merge_seed`, convert to read-modify-write deep-merge into the existing
  document (caller's `permissions` keys win, seed's other keys survive,
  create-if-absent) — new `host_actions.apply_cli_config(host, cfg)` beside
  `build_cli_config`; call it post-seed on fresh and post-restore on resume
  (resume site currently at session.py:320-331 area). Delete the
  "seed wins — the claudecode plant-then-merge pattern" comment
  (session.py:359-362) — that pattern was repudiated. Tests: seeded-fresh
  caller-wins-per-key + seed-extra-keys-survive.
- **opencode (T3):** fix the resume gap. The `opencode.json` build+write block
  (`session.py:329-349`) runs only under `if not resuming:` — caller
  `opencode_config` is never re-applied on resume (snapshot freezes
  first-launch config). Hoist the block so it also runs on the resume path
  AFTER the workdir restore. The file is wholly optio-generated (seed never
  carries it) so the whole-file write stays. Test: resume path re-applies a
  changed `opencode_config`.

## Task 9 — optio-agents-all: entry-point coverage (PARALLEL with 2–8)

Files: `packages/optio-agents-all/src/optio_agents_all/*` (only if needed —
`create_task` forwards configs verbatim, expected no functional change),
`packages/optio-agents-all/tests/test_config_uniformity.py` (new),
`packages/optio-agents-all/AGENTS.md`, root `AGENTS.md`.

- Uniformity guard test: every member of the `AgentTaskConfig` union is a
  subclass of `BlobCryptoConfigMixin` (and `ClaustrumConfigMixin`); constructing
  each with an asymmetric seed pair raises `ValueError`; `seed_encrypt` fallback
  behaves identically across all 7.
- `create_task` passthrough: assert the dispatcher hands the config object
  through untouched (identity), so the crypto channel needs no entry-point code.
- AGENTS.md (package + root unified reference): document `seed_blob_*` as a
  uniform config field set across all 7 engines via the mixin.

## Task 10 — verification (SEQUENTIAL, LAST, main loop)

- `docker run -d --rm --name optio-test-mongo -p 27017:27017 mongo:7`
- `make test` → must be green; fix fallout (small fixes committed to the
  touched package's paths); stop the container.
- Straggler greps: no engine still passes `session_blob_*` to
  `merge_seed`/`capture_seed`/`save_back_if_changed`/`run_credential_watcher`
  seed sites; no remaining local `session_blob_encrypt` field declarations in
  engine `types.py`; no `seed_blob_encrypt or session_blob_encrypt` inline
  expressions outside the mixin.
