# vultus

Library-shaped subtree. v0 incubation site for a future `@yourorg/vultus-*` set of packages.

## Two strict rules (ESLint-enforced)

1. **No imports from excavator-specific code** inside `vultus/`. No `@excavator/contracts`, no `shared/api/error-routes`, no i18n keys. The subtree is parameterized via generics; consumers (`shared/hooks/*`) wire excavator types in.
2. **No business logic** in `vultus/`. Only descriptor types, builder hooks, and rendering primitives.

ESLint guards both rules via `no-restricted-imports` patterns applied to files under `vultus/`.

## When extracted

The migration to `@yourorg/vultus-core` + `@yourorg/vultus-antd` packages is a module move + import-path rewrite at consumer sites — no design changes inside vultus, no call-site rewrites in pages. See `docs/vultus-library-seed.md`.
