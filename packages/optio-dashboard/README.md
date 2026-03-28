# optio-dashboard

Standalone management UI for Optio. Bundles optio-api and optio-ui into a single deployable app — no custom backend or frontend required.

## Quick Start

```bash
npx optio-dashboard
```

Or install and run:

```bash
npm install optio-dashboard
optio-dashboard
```

## Configuration

All configuration is via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `MONGODB_URL` | `mongodb://localhost:27017/optio` | MongoDB connection string (database name extracted from URL path) |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection string |
| `OPTIO_PREFIX` | `optio` | Namespace prefix for MongoDB collections and Redis streams |
| `PORT` | `3000` | HTTP port to listen on |

Copy `.env.example` to `.env` and adjust as needed.

## What This Is

A thin wrapper around [optio-api](../optio-api/README.md) and [optio-ui](../optio-ui/README.md). If you need custom API endpoints, custom UI components, or want to embed Optio into an existing application, use those packages directly instead.

## See Also

- [Optio Overview](../../README.md)
