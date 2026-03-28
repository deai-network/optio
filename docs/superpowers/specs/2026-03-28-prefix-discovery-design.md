# Prefix Auto-Discovery

## Problem

For optio-api and optio-ui to work with an optio-core instance using a non-default prefix, the same prefix must be configured in each layer. This is an unnecessary configuration burden — the prefix can be inferred from the database.

## Solution

Detect active prefixes by scanning MongoDB collection names and validating document schemas. Expose the results via a new API endpoint. Update the UI provider to auto-discover when no explicit prefix is given. Make the dashboard zero-config for prefix selection.

## Detection Logic (optio-api)

New `discoverPrefixes(db)` async function:

1. Call `db.listCollections()` to get all collection names.
2. Filter for names matching `*_processes` and extract candidate prefixes (everything before `_processes`).
3. For each candidate, sample one document and verify optio-specific fields: `processId`, `rootId`, `depth`.
4. Return confirmed prefix strings as an array (e.g. `["optio", "myapp"]`).

This is a plain async function with no framework coupling.

## API Endpoint

New prefix-independent route: `GET /api/optio/prefixes`

Response: `{ "prefixes": ["optio", "myapp"] }`

Empty database returns `{ "prefixes": [] }`.

Added to the ts-rest contract in optio-contracts. Exposed by all adapters (Fastify, Express, Next.js App, Next.js Pages) alongside existing `:prefix` routes.

## UI Primitives (optio-ui)

Two new hooks:

- **`usePrefixes()`** — calls `GET /api/optio/prefixes` via the ts-rest client (using `baseUrl` from `OptioContext`). Returns standard query result (data, loading, error).
- **`usePrefixDiscovery()`** — convenience wrapper over `usePrefixes()`. If exactly one prefix found, returns it. If zero or multiple, returns `null`. Exposes the full list either way.

No new UI components. Host apps decide presentation.

### OptioProvider Changes

The existing `OptioProvider` accepts an optional `prefix` prop. Updated resolution:

1. Always call `usePrefixDiscovery()` internally (rules of hooks require unconditional calls).
2. Effective prefix priority: explicit prop > discovered single prefix > `"optio"` default.
3. No loading state — use `"optio"` immediately, switch when discovery resolves.

## Dashboard Changes

- Remove `OPTIO_PREFIX` env var entirely.
- Use `usePrefixes()` to get the list.
- One prefix: use it automatically.
- Multiple prefixes: show a dropdown selector.
- Zero prefixes: show a message that no optio instance was detected.

## Testing

- **API endpoint**: Integration test via Fastify adapter hitting `GET /api/optio/prefixes`.
- **UI hooks**: Unit tests for `usePrefixes()` and `usePrefixDiscovery()` with mocked API responses (zero, one, multiple prefixes).
- **OptioProvider**: Test the priority chain — explicit prop wins, then discovery, then `"optio"` default.
