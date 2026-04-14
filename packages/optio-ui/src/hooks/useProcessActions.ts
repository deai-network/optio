import { useQueryClient } from '@tanstack/react-query';
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
    launch: (processId: string) => launchMutation.mutate({ params: { id: processId }, query: { database, prefix } }),
    cancel: (processId: string) => cancelMutation.mutate({ params: { id: processId }, query: { database, prefix } }),
    dismiss: (processId: string) => dismissMutation.mutate({ params: { id: processId }, query: { database, prefix } }),
    resync: () => resyncMutation.mutate({ query: { database, prefix }, body: {} }),
    resyncClean: () => resyncMutation.mutate({ query: { database, prefix }, body: { clean: true } }),
    isResyncing: resyncMutation.isPending,
  };
}
