#!/usr/bin/env bash
# Phase-3 hardened interop test for optio engine ↔ TS clamator client.
# Hard per-step timeouts, distinct exit codes, real-time log tailing.
#
# Exit codes:
#   0   success
#   10  docker pre-flight failed (no docker daemon, missing image, pull failed)
#   11  redis not ready within bound
#   12  mongo not ready within bound
#   13  engine not ready within bound
#   14  fastify not ready within bound (Stage B; unused in A0)
#   15  scenario assertion failed
#   16  cleanup error (best-effort, not fatal)
#   124 outer timeout (script wall-clock exceeded)
#
# Env knobs:
#   INTEROP_DEBUG=1   verbose mode, increased timeouts (30/30/60s instead of 5/10/15s)
#   INTEROP_KEEP=1    skip cleanup on failure for postmortem

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEMO_DIR="$REPO_ROOT/packages/optio-demo"

# Bounds (overridable via INTEROP_DEBUG).
if [[ "${INTEROP_DEBUG:-0}" == "1" ]]; then
  REDIS_TIMEOUT=30
  MONGO_TIMEOUT=30
  ENGINE_TIMEOUT=60
  SCENARIO_TIMEOUT=120
else
  REDIS_TIMEOUT=5
  MONGO_TIMEOUT=10
  ENGINE_TIMEOUT=15
  SCENARIO_TIMEOUT=60
fi

ENGINE_LOG="/tmp/optio-interop-engine.log"
> "$ENGINE_LOG"

phase() { echo "[interop] phase=$1"; }
die() { local code="$1"; shift; echo "[interop] ERROR: $*" >&2; exit "$code"; }

cleanup() {
  set +e
  phase cleanup
  if [[ "${INTEROP_KEEP:-0}" == "1" && "${EXIT_CODE:-0}" -ne 0 ]]; then
    echo "[interop] INTEROP_KEEP=1: skipping container/process cleanup for postmortem" >&2
    echo "[interop] engine log: $ENGINE_LOG" >&2
    [[ -n "${REDIS_CID:-}" ]] && echo "[interop] redis CID: $REDIS_CID" >&2
    [[ -n "${MONGO_CID:-}" ]] && echo "[interop] mongo CID: $MONGO_CID" >&2
    [[ -n "${ENGINE_PID:-}" ]] && echo "[interop] engine PID: $ENGINE_PID" >&2
    return 0
  fi
  if [[ -n "${ENGINE_PID:-}" ]]; then
    kill -9 "$ENGINE_PID" 2>/dev/null
    wait "$ENGINE_PID" 2>/dev/null
  fi
  [[ -n "${REDIS_CID:-}" ]] && docker rm -f "$REDIS_CID" >/dev/null 2>&1
  [[ -n "${MONGO_CID:-}" ]] && docker rm -f "$MONGO_CID" >/dev/null 2>&1
}
trap 'EXIT_CODE=$?; cleanup; exit $EXIT_CODE' EXIT

# Pre-flight: docker daemon + images.
phase docker-pre-flight
docker info >/dev/null 2>&1 || die 10 "docker daemon not reachable"
for img in redis:7 mongo:7; do
  if ! docker image inspect "$img" >/dev/null 2>&1; then
    echo "[interop] pulling $img..."
    timeout 30 docker pull "$img" >/dev/null 2>&1 || die 10 "pull failed for $img"
  fi
done

# Allocate free ports.
REDIS_PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()")
MONGO_PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()")

# Start redis.
phase redis-up
REDIS_CID=$(docker run -d -p "${REDIS_PORT}:6379" redis:7)
DEADLINE=$(( $(date +%s) + REDIS_TIMEOUT ))
while (( $(date +%s) < DEADLINE )); do
  if docker exec "$REDIS_CID" redis-cli ping 2>/dev/null | grep -q PONG; then
    echo "[interop] redis ready on port $REDIS_PORT"
    break
  fi
  sleep 0.2
done
if ! docker exec "$REDIS_CID" redis-cli ping 2>/dev/null | grep -q PONG; then
  docker logs --tail 50 "$REDIS_CID" >&2 2>/dev/null
  die 11 "redis not ready within ${REDIS_TIMEOUT}s"
fi

# Start mongo.
phase mongo-up
MONGO_CID=$(docker run -d -p "${MONGO_PORT}:27017" --tmpfs /data/db:rw,noexec,nosuid mongo:7)
DEADLINE=$(( $(date +%s) + MONGO_TIMEOUT ))
while (( $(date +%s) < DEADLINE )); do
  if docker exec "$MONGO_CID" mongosh --eval 'quit(db.adminCommand("ping").ok ? 0 : 1)' --quiet >/dev/null 2>&1; then
    echo "[interop] mongo ready on port $MONGO_PORT"
    break
  fi
  sleep 0.4
done
if ! docker exec "$MONGO_CID" mongosh --eval 'quit(db.adminCommand("ping").ok ? 0 : 1)' --quiet >/dev/null 2>&1; then
  docker logs --tail 50 "$MONGO_CID" >&2 2>/dev/null
  die 12 "mongo not ready within ${MONGO_TIMEOUT}s"
fi

# Start engine.
phase engine-up
MONGODB_URL="mongodb://localhost:${MONGO_PORT}/optio-demo" \
  REDIS_URL="redis://localhost:${REDIS_PORT}" \
  OPTIO_PREFIX="optio" \
  python -m optio_demo > >(tee -a "$ENGINE_LOG" | sed 's/^/[engine] /') 2> >(tee -a "$ENGINE_LOG" | sed 's/^/[engine] /' >&2) &
ENGINE_PID=$!

HEARTBEAT_KEY="optio-demo/optio:heartbeat"
DEADLINE=$(( $(date +%s) + ENGINE_TIMEOUT ))
while (( $(date +%s) < DEADLINE )); do
  if docker exec "$REDIS_CID" redis-cli exists "$HEARTBEAT_KEY" 2>/dev/null | grep -q '^1$'; then
    echo "[interop] engine ready (heartbeat present)"
    break
  fi
  if ! kill -0 "$ENGINE_PID" 2>/dev/null; then
    tail -50 "$ENGINE_LOG" >&2
    die 13 "engine subprocess exited before becoming ready"
  fi
  sleep 0.2
done
if ! docker exec "$REDIS_CID" redis-cli exists "$HEARTBEAT_KEY" 2>/dev/null | grep -q '^1$'; then
  tail -50 "$ENGINE_LOG" >&2
  die 13 "engine not ready within ${ENGINE_TIMEOUT}s"
fi

# Run scenarios.
phase running-scenarios
set +e
REDIS_URL="redis://localhost:${REDIS_PORT}" \
  timeout "$SCENARIO_TIMEOUT" pnpm --dir "$DEMO_DIR/interop" exec tsx run.ts
SCENARIO_EXIT=$?
set -e
if (( SCENARIO_EXIT != 0 )); then
  if (( SCENARIO_EXIT == 124 )); then
    die 15 "scenario runner timed out after ${SCENARIO_TIMEOUT}s"
  fi
  die 15 "scenario runner exited $SCENARIO_EXIT"
fi
echo "[interop] scenarios passed"
exit 0
