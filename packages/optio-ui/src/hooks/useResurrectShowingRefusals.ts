import { useProcessActions } from './useProcessActions.js';
import { useShowRefusal } from './useShowRefusal.js';

/**
 * `useProcessActions().resurrect`, showing a refusal, as `useLaunchShowingRefusals` does for a
 * launch: it resurrects with `resurrectAsync`, and a rejection goes through `optioErrorRoutes`
 * (route `processes.resurrect`) to vultus-core's MessageSink:
 * `t('errors.processes.resurrect.<reason>')`, else the error's message, else `t('common.error')`.
 * An accepted resurrect shows nothing. Made for an `onResurrect` (LaunchControls, ProcessItem,
 * ProcessList, ProcessTreeView); it returns nothing.
 */
export function useResurrectShowingRefusals(): (processId: string) => void {
  const { resurrectAsync } = useProcessActions();
  const showRefusal = useShowRefusal('processes.resurrect');
  const resurrectShowingRefusal = async (processId: string) => {
    try {
      await resurrectAsync(processId);
    } catch (err) {
      showRefusal(err);
    }
  };
  return (processId) => {
    void resurrectShowingRefusal(processId);
  };
}
