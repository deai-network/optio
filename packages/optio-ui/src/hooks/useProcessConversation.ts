import { useOptioPrefix, useOptioClient, useOptioDatabase } from '../context/useOptioContext.js';
import { useProcess } from './useProcessQueries.js';

/**
 * Reads a suspended process's saved conversation record
 * (`GET /processes/:id/conversation`, proxied by optio-api from the engine's
 * conversation archive). Returns the raw ts-rest react-query result:
 *   - `data.body` is `{ events, capturedAt }` on a 200;
 *   - a non-2xx response (401/403/404/409/500/503) surfaces as `error`
 *     (`error.body` carries `{ reason, message }` for every status except
 *     401, which is `{ message }` only — ts-rest throws non-2xx responses
 *     rather than returning them as data).
 *
 * (The result is typed loosely — `OptioClient` is `any`-typed, so this
 * returns `any` — but its shape at runtime is exactly the ts-rest
 * react-query `UseQueryResult` described above.)
 *
 * The query key includes the process's current `status.stoppedAt` (the
 * timestamp a suspend — a cancel with saved state — sets) alongside the
 * process id. Without it, a resume-then-resuspend cycle would keep the first
 * suspend's cached conversation visible (briefly, or indefinitely if the
 * hook never remounts) while the new one loads; keying on the timestamp
 * makes the second suspend a distinct cache entry instead.
 *
 * `opts.stoppedAt` lets the caller supply that timestamp directly (e.g. a
 * suspended widget already has it on its `process.status.stoppedAt` prop).
 * Passing it — even explicitly `null` — skips this hook's own process-doc
 * fetch (and its 5s poll) entirely; omitting it falls back to reading it via
 * an internal `useProcess(processId)` call, as before. `opts.enabled: false`
 * also skips the process-doc fetch (as well as the conversation fetch) —
 * nothing fetches at all.
 *
 * `staleTime: Infinity`: a record never changes for a given suspend (whose
 * stoppedAt is in the key), so a window refocus or a remount does not refetch
 * it, and the engine does not decrypt it again. `refetch()` still fetches.
 *
 * Retries: see `conversationRetry` / `conversationRetryDelay`. A 503 (no
 * engine conversation archive reachable) is retried long enough to outlast an
 * engine restart: a host page that is open while the engine restarts reads the
 * record of the very suspend that restart caused, while the archive is down.
 */
// A 503 (the engine's conversation archive is not reachable, e.g. the engine
// is restarting): six retries, 1 + 2 + 4 + 8 + 15 + 15 s (~45 s), so a read
// started at the beginning of a restart still gets the record.
const UNAVAILABLE_RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 15000, 15000];
// Other transient failures (a network blip, a 500): two retries.
const TRANSIENT_RETRIES = 2;

function errorStatus(error: unknown): number | undefined {
  const status = (error as { status?: number } | null)?.status;
  return typeof status === 'number' ? status : undefined;
}

/** Whether to retry the conversation read after `failureCount` failures. A 4xx
 * never (not-found / not-suspended / no-conversation: asking again does not
 * change the process); a 503 through UNAVAILABLE_RETRY_DELAYS_MS; anything
 * else TRANSIENT_RETRIES times. */
export function conversationRetry(failureCount: number, error: unknown): boolean {
  const status = errorStatus(error);
  if (status !== undefined && status >= 400 && status < 500) return false;
  if (status === 503) return failureCount < UNAVAILABLE_RETRY_DELAYS_MS.length;
  return failureCount < TRANSIENT_RETRIES;
}

/** The wait before retry `attempt` (0-based): the 503 schedule, else
 * react-query's default exponential backoff (1 s, 2 s, ... up to 30 s). */
export function conversationRetryDelay(attempt: number, error: unknown): number {
  if (errorStatus(error) === 503) {
    return UNAVAILABLE_RETRY_DELAYS_MS[Math.min(attempt, UNAVAILABLE_RETRY_DELAYS_MS.length - 1)];
  }
  return Math.min(1000 * 2 ** attempt, 30000);
}

export function useProcessConversation(
  processId: string,
  opts?: { enabled?: boolean; stoppedAt?: string | null },
) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();

  const enabled = opts?.enabled ?? true;
  const stoppedAtProvided = !!opts && Object.prototype.hasOwnProperty.call(opts, 'stoppedAt');

  // Skip the internal process-doc fetch (and its 5s poll) whenever the caller
  // already supplied `stoppedAt` (it has the process doc already) or whenever
  // this hook is disabled outright — passing `undefined` as the id is what
  // useProcess already treats as "disabled" (no fetch, no poll). Called
  // unconditionally either way, so Rules of Hooks holds.
  const { process } = useProcess((stoppedAtProvided || !enabled) ? undefined : processId);

  const statusTimestamp = stoppedAtProvided
    ? (opts!.stoppedAt ?? null)
    : (process as { status?: { stoppedAt?: string } } | null)?.status?.stoppedAt ?? null;

  return api.processes.getConversation.useQuery({
    queryKey: ['process-conversation', database, prefix, processId, statusTimestamp],
    queryData: { params: { id: processId }, query: { database, prefix } },
    enabled: !!processId && enabled,
    // A record is fixed for a given suspend, and its stoppedAt is in the key:
    // never stale, so a window refocus or a remount does not make the engine
    // decrypt it again. An explicit refetch() (a Retry) still fetches.
    staleTime: Infinity,
    // Never a 4xx; a 503 long enough to outlast an engine restart; other
    // transient failures briefly (conversationRetry / conversationRetryDelay).
    retry: conversationRetry,
    retryDelay: conversationRetryDelay,
  });
}
