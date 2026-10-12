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
 */
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
    // Never retry a 4xx (not-found / not-suspended / no-conversation / etc.) —
    // the process state won't change because we asked again. Give transient
    // failures (network blip, 500, 503) a couple of attempts.
    retry: (failureCount: number, error: unknown) => {
      const status = (error as { status?: number } | null)?.status;
      if (typeof status === 'number' && status >= 400 && status < 500) return false;
      return failureCount < 2;
    },
  });
}
