# Authentication System

**Base revision:** `7a0e50100fa419ef4816e61e8ca0f87768de34bd` on branch `main` (as of 2026-04-14T03:27:58Z)

## Summary

Add authentication to `optio-dashboard` using Better Auth. The API and UI packages remain auth-agnostic; all auth logic lives in the dashboard. The `optio-api` package gains a mandatory `authenticate` parameter with both compile-time and runtime enforcement.

## Scope

**Changed packages:**
- `optio-api`: make `authenticate` mandatory; add runtime guard
- `optio-dashboard`: add Better Auth (server + client), startup password validation, login UI

**Unchanged packages:** `optio-ui`, `optio-core`, `optio-contracts`, all others.

---

## 1. optio-api: Mandatory authenticate parameter

The `authenticate` parameter in all four adapter option types (`express`, `fastify`, `nextjs-pages`, `nextjs-app`) becomes required:

- **Compile-time:** Removing `| undefined` from the type makes omitting it a TypeScript error.
- **Runtime:** Each adapter throws an exception immediately if `authenticate` is falsy. This catches JavaScript callers and type-cast workarounds.

No other behaviour changes in `optio-api`.

---

## 2. optio-dashboard: Server-side auth

### Startup sequence

1. Read `OPTIO_PASSWORD` from the environment.
   - If absent or empty: print `"Error: OPTIO_PASSWORD environment variable is required"` and exit with a non-zero code. No server starts.
2. Initialize Better Auth with:
   - The MongoDB adapter, sharing the existing `db` connection.
   - The `emailAndPassword` plugin.
3. Upsert a single virtual admin user (`admin@optio.local` / `OPTIO_PASSWORD`) into Better Auth's users collection. This keeps the stored password in sync if the env var changes between restarts.
   - The upsert goes through Better Auth's internal adapter (`ctx.internalAdapter.createUser` + `linkAccount` on first boot, `ctx.internalAdapter.updatePassword` on subsequent boots) and hashes via `ctx.password.hash`. No direct MongoDB collection writes — the dashboard does not touch `user` / `account` documents outside the library.
4. Configure `emailAndPassword.disableSignUp: true`. Better Auth's core always registers `/sign-up/email` in its router; this flag is what makes the handler reject external sign-ups. The server-side bootstrap above uses the internal adapter, so disabling HTTP sign-up does not affect it. This aligns with the product intent that `OPTIO_PASSWORD` is the single authentication secret and no self-registration is possible.

### Request routing

- Better Auth's request handler is mounted at `/api/auth/*` on the Fastify server. This automatically provides `POST /api/auth/sign-in/email` and related session endpoints.
- The `authenticate` callback passed to `registerOptioApi` calls `auth.api.getSession(request)`:
  - Valid session → return `'operator'`
  - No session or invalid session → return `null` (results in 401)

---

## 3. optio-dashboard: Client-side auth

### Auth gate

The React app wraps its existing content in a thin auth gate component:
- On load, the Better Auth React client checks for an active session via `useSession()`.
- **No session:** render the `better-auth-ui` sign-in screen full-page (replaces the dashboard entirely).
- **Active session:** render the existing dashboard as today.
- A sign-out button is added to the dashboard header. On click, the session is destroyed and the gate returns to the sign-in screen.

### Better Auth client setup

- A single `createAuthClient()` instance is configured at the same origin (`/api/auth`).
- `<AuthUIProvider>` from `better-auth-ui` wraps the app and receives this client.
- `<SignInCard>` (or equivalent `better-auth-ui` component) is rendered when unauthenticated.

### CSS coexistence

`better-auth-ui` requires Tailwind CSS and shadcn/ui. The login screen renders in place of the dashboard (not nested inside Ant Design layout), so CSS conflicts are expected to be minimal. This is a first-iteration trial. If the result is visually broken, the follow-up is to replace `better-auth-ui` with a hand-rolled Ant Design `Modal` + `Form` login dialog using Better Auth's React hooks directly.

### Session propagation

Better Auth's React client handles attaching the session cookie to API requests automatically. No manual `Authorization` header wiring is needed on the client side.

---

## Open questions / follow-up

- If shadcn/Tailwind visually conflicts with Ant Design, replace the login UI with a custom Ant Design form (out of scope for this iteration).
- Role granularity: currently all authenticated users get `'operator'`. Viewer-only access is not in scope.
