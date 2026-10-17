# optio-dashboard

Standalone management UI for Optio. Bundles optio-api and optio-ui into a single deployable app — no custom backend or frontend required.

## Quick Start

```bash
OPTIO_PASSWORD=mysecret npx optio-dashboard
```

Or install and run:

```bash
npm install optio-dashboard
OPTIO_PASSWORD=mysecret optio-dashboard
```

Then open `http://localhost:3000` and sign in with that password.

## Configuration

All configuration is via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPTIO_PASSWORD` | (required) | The sign-in password. The dashboard exits without it. Changing it ends all sessions. |
| `MONGODB_URL` | `mongodb://localhost:27017/optio` | MongoDB connection string. Put the database in it: naming a database (the URL path), it shows only that database and keeps its logins there. Without a path, or unset, it discovers optio instances in every database on the server and keeps its logins in a database named `optio`. |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection string |
| `OPTIO_PREFIX` | (any) | Shows only optio instances with this prefix. |
| `PORT` | `3000` | HTTP port to listen on, on all interfaces |
| `OPTIO_TRUSTED_ORIGINS` | (none) | Extra origins sign-in is allowed from, comma-separated (e.g. `http://myhost:3000`) |
| `OPTIO_VERBOSE` | (off) | `1` or `true` (or the `--verbose` / `-v` flag) logs every request |

Nothing reads a `.env` file: set the variables in the environment.

Sign-in works only from `http://localhost:<PORT>`: from `127.0.0.1` or a machine name it
fails with "Invalid origin" until that origin is in `OPTIO_TRUSTED_ORIGINS`.

With both `MONGODB_URL` (naming a database) and `OPTIO_PREFIX` set, the dashboard is
pinned to that one instance: there is nothing to pick, so it shows the instance's name
instead of the instance selector.

## What This Is

A thin wrapper around [optio-api](../optio-api/README.md) and [optio-ui](../optio-ui/README.md). If you need custom API endpoints, custom UI components, or want to embed Optio into an existing application, use those packages directly instead.

## See Also

- [Optio Overview](../../README.md)
