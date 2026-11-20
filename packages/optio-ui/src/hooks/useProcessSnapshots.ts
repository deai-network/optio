import type { ProcessSnapshot } from 'optio-contracts';
import { useOptioPrefix, useOptioClient, useOptioDatabase } from '../context/useOptioContext.js';
import { shareTsRestResponse } from '../ts-rest-sharing.js';

/**
 * A process's valid snapshots, newest first, the newest included
 * (`GET /processes/:id/snapshots`): what a resume from an older snapshot picks
 * from (`useProcessActions().launch(id, { resume: true, resumeFrom: capturedAt })`).
 * `processId` is the process's `_id`.
 *
 * Returns undefined while disabled (`opts.enabled` false, or no id), while
 * loading, and when the read failed (a 404: no such process, or its task keeps
 * no snapshot history). A failure throws nothing and is not retried: the
 * caller then offers what it offers without the list.
 *
 * The query key carries `opts.runEndedAt` (`runEndedAt(process)`, required:
 * each end of a run may add a snapshot, so each reads the list again). For a
 * given end the list is fixed (`staleTime: Infinity`), so a refocus or a
 * remount does not ask the engine again. A snapshot written without a run end
 * (a resurrect's capture) is re-read because `useProcessActions`' launch and
 * resurrect invalidate the process's lists when they settle.
 */
export function useProcessSnapshots(
  processId: string | undefined,
  opts: { enabled: boolean; runEndedAt: string | null },
): ProcessSnapshot[] | undefined {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();
  const enabled = !!processId && opts.enabled;

  const { data } = api.processes.getSnapshots.useQuery({
    queryKey: ['process-snapshots', database, prefix, processId, opts.runEndedAt],
    queryData: { params: { id: processId! }, query: { database, prefix } },
    enabled,
    staleTime: Infinity,
    retry: false,
    structuralSharing: shareTsRestResponse,
  });
  return enabled && data?.status === 200 ? data.body.snapshots : undefined;
}
