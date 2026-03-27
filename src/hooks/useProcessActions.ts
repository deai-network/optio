import { useQueryClient } from '@tanstack/react-query';
import { useOptioPrefix, useOptioClient } from '../context/useOptioContext.js';

interface ProcessActionsOptions {
  onResyncSuccess?: (clean: boolean) => void;
}

export function useProcessActions(options?: ProcessActionsOptions) {
  const prefix = useOptioPrefix();
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
    launch: (processId: string) => launchMutation.mutate({ params: { prefix, id: processId } }),
    cancel: (processId: string) => cancelMutation.mutate({ params: { prefix, id: processId } }),
    dismiss: (processId: string) => dismissMutation.mutate({ params: { prefix, id: processId } }),
    resync: () => resyncMutation.mutate({ params: { prefix }, body: {} }),
    resyncClean: () => resyncMutation.mutate({ params: { prefix }, body: { clean: true } }),
    isResyncing: resyncMutation.isPending,
  };
}
