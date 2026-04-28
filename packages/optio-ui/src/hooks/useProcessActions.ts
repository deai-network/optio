import { useQueryClient } from '@tanstack/react-query';
import type { ProcessMetadataFilter } from 'optio-contracts';
import { useOptioPrefix, useOptioClient, useOptioDatabase } from '../context/useOptioContext.js';

interface ProcessActionsOptions {
  onResyncSuccess?: (clean: boolean) => void;
}

export function useProcessActions(options?: ProcessActionsOptions) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();
  const queryClient = useQueryClient();

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['processes'] });

  const launchMutation = api.processes.launch.useMutation({ onSuccess: invalidate });
  const cancelMutation = api.processes.cancel.useMutation({ onSuccess: invalidate });
  const dismissMutation = api.processes.dismiss.useMutation({ onSuccess: invalidate });
  const resyncMutation = api.processes.resync.useMutation({
    onSuccess: (_data: any, variables: any) => {
      options?.onResyncSuccess?.(variables.body?.clean ?? false);
      invalidate();
    },
  });

  return {
    launch: (processId: string, opts?: { resume?: boolean }) =>
      launchMutation.mutate({
        params: { id: processId },
        query: { database, prefix },
        body: opts?.resume === true ? { resume: true } : {},
      }),
    cancel: (processId: string) => cancelMutation.mutate({ params: { id: processId }, query: { database, prefix } }),
    dismiss: (processId: string) => dismissMutation.mutate({ params: { id: processId }, query: { database, prefix } }),
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
