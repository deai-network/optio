#!/usr/bin/env bash
# Phase-2 interop test for optio engine ↔ TS clamator client.
# Spins ephemeral docker redis + mongodb + python engine subprocess + node test runner.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEMO_DIR="$REPO_ROOT/packages/optio-demo"

# Find free ports to avoid conflicts with other running services.
REDIS_PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()")
MONGO_PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()")

REDIS_CID=""
MONGO_CID=""

cleanup() {
  set +e
  if [[ -n "${ENGINE_PID:-}" ]]; then
    kill "$ENGINE_PID" 2>/dev/null
    wait "$ENGINE_PID" 2>/dev/null
  fi
  [[ -n "$REDIS_CID" ]] && docker rm -f "$REDIS_CID" >/dev/null 2>&1
  [[ -n "$MONGO_CID" ]] && docker rm -f "$MONGO_CID" >/dev/null 2>&1
}
trap cleanup EXIT

echo "[interop] starting redis on port $REDIS_PORT..."
REDIS_CID=$(docker run -d -p "${REDIS_PORT}:6379" redis:7)

echo "[interop] starting mongodb on port $MONGO_PORT..."
# Use --tmpfs to store data in RAM — avoids docker overlay disk-space pressure.
MONGO_CID=$(docker run -d -p "${MONGO_PORT}:27017" --tmpfs /data/db:rw,noexec,nosuid mongo:7)

echo "[interop] waiting for redis ready..."
for i in {1..50}; do
  if docker exec "$REDIS_CID" redis-cli ping 2>/dev/null | grep -q PONG; then
    echo "[interop] redis ready."
    break
  fi
  sleep 0.2
done

echo "[interop] waiting for mongodb ready..."
for i in {1..75}; do
  if docker exec "$MONGO_CID" mongosh --eval "db.adminCommand('ping')" --quiet 2>/dev/null | grep -q '"ok": 1'; then
    echo "[interop] mongodb ready."
    break
  fi
  sleep 0.4
done

echo "[interop] starting optio-demo engine..."
MONGODB_URL="mongodb://localhost:${MONGO_PORT}/optio-demo" \
  REDIS_URL="redis://localhost:${REDIS_PORT}" \
  OPTIO_PREFIX="optio" \
  python -m optio_demo &
ENGINE_PID=$!

echo "[interop] waiting for engine heartbeat..."
HEARTBEAT_KEY="optio-demo/optio:heartbeat"
READY=0
for i in {1..150}; do
  if docker exec "$REDIS_CID" redis-cli exists "$HEARTBEAT_KEY" 2>/dev/null | grep -q '^1$'; then
    READY=1
    echo "[interop] engine ready."
    break
  fi
  sleep 0.2
done

if [[ "$READY" -eq 0 ]]; then
  echo "[interop] ERROR: engine did not become ready within 30s" >&2
  exit 1
fi

echo "[interop] running scenarios..."
REDIS_URL="redis://localhost:${REDIS_PORT}" \
  pnpm --dir "$DEMO_DIR/interop" exec tsx run.ts
EXIT=$?

exit $EXIT
