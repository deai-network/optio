import { useQueryClient } from '@tanstack/react-query';
import type { Process, ProcessMetadataFilter } from 'optio-contracts';
import { useOptioPrefix, useOptioClient, useOptioDatabase } from '../context/useOptioContext.js';
import { getSessionId } from '../session/sessionEvents.js';

interface ProcessActionsOptions {
  onResyncSuccess?: (clean: boolean) => void;
}

export interface LaunchOptions {
  resume?: boolean;
  /** Resume from an older snapshot: the `capturedAt` `useProcessSnapshots` listed for it. Needs
   *  `resume: true` (else the launch is refused, `invalid-request`); the newer snapshots are discarded. */
  resumeFrom?: string;
  /** A task factory's values, by parameter id: the launch makes the factory's child with them. */
  params?: Record<string, unknown>;
  /** A task factory's launch: removed when it ends (true), or kept until dismissed (false); else the factory's default. */
  ephemeral?: boolean;
}

/** The launch route's body: this tab's session, `resume: true` when asked, `resumeFrom` when set,
 *  a task factory's values and `ephemeral`. */
function launchBody(opts: LaunchOptions | undefined) {
  return {
    sessionId: getSessionId(),
    ...(opts?.resume === true ? { resume: true } : {}),
    ...(opts?.resumeFrom !== undefined ? { resumeFrom: opts.resumeFrom } : {}),
    ...(opts?.params !== undefined ? { params: opts.params } : {}),
    ...(opts?.ephemeral !== undefined ? { ephemeral: opts.ephemeral } : {}),
  };
}

export function useProcessActions(options?: ProcessActionsOptions) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();
  const queryClient = useQueryClient();

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['processes'] });
  // A launch or resurrect may have written a snapshot without ending a run (a resurrect's capture
  // whose resume was refused), which useProcessSnapshots' runEndedAt key does not see: once it has
  // settled, success or refusal, the process's lists (any runEndedAt) are read again.
  const invalidateSnapshots = (_data: unknown, _error: unknown, variables: { params: { id: string } }) =>
    queryClient.invalidateQueries({ queryKey: ['process-snapshots', database, prefix, variables.params.id] });

  const launchMutation = api.processes.launch.useMutation({ onSuccess: invalidate, onSettled: invalidateSnapshots });
  const cancelMutation = api.processes.cancel.useMutation({ onSuccess: invalidate });
  const dismissMutation = api.processes.dismiss.useMutation({ onSuccess: invalidate });
  const resurrectMutation = api.processes.resurrect.useMutation({ onSuccess: invalidate, onSettled: invalidateSnapshots });
  const resyncMutation = api.processes.resync.useMutation({
    onSuccess: (_data: any, variables: any) => {
      options?.onResyncSuccess?.(variables.body?.clean ?? false);
      invalidate();
    },
  });

  const launchRequest = (processId: string, opts?: LaunchOptions) => ({
    params: { id: processId },
    query: { database, prefix },
    body: launchBody(opts),
  });
  const resurrectRequest = (processId: string) => ({
    params: { id: processId },
    query: { database, prefix },
    body: { sessionId: getSessionId() },
  });

  return {
    launch: (processId: string, opts?: LaunchOptions) => launchMutation.mutate(launchRequest(processId, opts)),
    /** As `launch`, for an action that shows the outcome (a vultus useAction): resolves with the
     *  launched record (a task factory's: the child it made), rejects with ts-rest's
     *  `{ status, body: { reason, message } }` on a refusal. */
    launchAsync: async (processId: string, opts?: LaunchOptions): Promise<Process> =>
      (await launchMutation.mutateAsync(launchRequest(processId, opts))).body,
    cancel: (processId: string) => cancelMutation.mutate({ params: { id: processId }, query: { database, prefix } }),
    dismiss: (processId: string) => dismissMutation.mutate({ params: { id: processId }, query: { database, prefix } }),
    /** As `dismiss`, for an action that shows the outcome (a vultus useAction): resolves when it is
     *  done (an ended factory launch is deleted by it), rejects with ts-rest's
     *  `{ status, body: { reason, message } }` on a refusal. */
    dismissAsync: async (processId: string): Promise<void> => {
      await dismissMutation.mutateAsync({ params: { id: processId }, query: { database, prefix } });
    },
    resurrect: (processId: string) => resurrectMutation.mutate(resurrectRequest(processId)),
    /** As `resurrect`, for a caller that shows the outcome: resolves with the process once the
     *  resurrect is accepted, rejects with ts-rest's `{ status, body: { reason, message } }` on a
     *  refusal. */
    resurrectAsync: async (processId: string): Promise<Process> =>
      (await resurrectMutation.mutateAsync(resurrectRequest(processId))).body,
    resync: (metadataFilter?: ProcessMetadataFilter) =>
      resyncMutation.mutate({
        query: { database, prefix },
        body: metadataFilter ? { metadataFilter } : {},
      }),
    resyncClean: (metadataFilter?: ProcessMetadataFilter) =>
      resyncMutation.mutate({
        query: { database, prefix },
        body: metadataFilter ? { clean: true, metadataFilter } : { clean: true },
      }),
    isResyncing: resyncMutation.isPending,
  };
}
