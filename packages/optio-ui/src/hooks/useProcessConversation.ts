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
 * The query key includes the process's current `status.stoppedAt` (the
 * timestamp a suspend — a cancel with saved state — sets) alongside the
 * process id. Without it, a resume-then-resuspend cycle would keep the first
 * suspend's cached conversation visible (briefly, or indefinitely if the
 * hook never remounts) while the new one loads; keying on the timestamp
 * makes the second suspend a distinct cache entry instead.
 */
export function useProcessConversation(
  processId: string,
  opts?: { enabled?: boolean },
) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();

  const { process } = useProcess(processId);
  const statusTimestamp = (process as { status?: { stoppedAt?: string } } | null)?.status?.stoppedAt ?? null;

  return api.processes.getConversation.useQuery({
    queryKey: ['process-conversation', database, prefix, processId, statusTimestamp],
    queryData: { params: { id: processId }, query: { database, prefix } },
    enabled: !!processId && (opts?.enabled ?? true),
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
