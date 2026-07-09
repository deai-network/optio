// Provider
export { OptioProvider } from './context/OptioProvider.js';
export { useOptioLive } from './context/useOptioContext.js';
export { getSessionId, resetSession } from './session/sessionEvents.js';
export type { SessionEventCallbacks } from './session/sessionEvents.js';
export {
  setBrowserOpenHandler,
  defaultHandleBrowserOpenRequests,
} from './handlers/browserOpen.js';
export type { BrowserOpenHandler, BrowserOpenRequest } from './handlers/browserOpen.js';

// Multi-process stream
export {
  MultiProcessStreamProvider,
  MultiProcessStreamContext,
} from './context/MultiProcessStreamContext.js';
export type {
  ProcessStreamSlice,
  MultiProcessUpdate,
  MultiLogEntry,
  MultiProcessTreeNode,
  MultiProcessStreamContextValue,
} from './context/MultiProcessStreamContext.js';

// Components
export { ProcessList } from './components/ProcessList.js';
export { ProcessItem } from './components/ProcessItem.js';
export type { ProcessItemProps } from './components/ProcessItem.js';
export { ProcessStatusBadge } from './components/ProcessStatusBadge.js';
export { ProcessTreeView } from './components/ProcessTreeView.js';
export { ProcessLogPanel } from './components/ProcessLogPanel.js';
export { WithFilteredProcesses, ProcessFilters, FilteredProcessList, useProcessFilter } from './components/ProcessFilter.js';

// Hooks
export { useInstances, useInstanceDiscovery, type OptioInstance } from './hooks/useInstanceDiscovery.js';
export { useProcessActions } from './hooks/useProcessActions.js';
export { useProcessList, useProcess, useProcesses, useProcessTree, useProcessTreeLog } from './hooks/useProcessQueries.js';
export { useProcessStream } from './hooks/useProcessStream.js';
export { useProcessListStream } from './hooks/useProcessListStream.js';
export { usePrefixes, usePrefixDiscovery } from './hooks/usePrefixDiscovery.js';

// Types
export type { FilterGroup } from './components/ProcessFilter.js';
export type { ProcessTreeNode } from './hooks/useProcessStream.js';

// Widgets
export { registerWidget } from './widgets/registry.js';
export type { WidgetProps, WidgetComponent } from './widgets/registry.js';
export { IframeWidget } from './widgets/IframeWidget.js';
export { IframeInputWidget } from './widgets/IframeInputWidget.js';

// Components
export { ProcessDetailView } from './components/ProcessDetailView.js';
export type { ProcessDetailViewProps } from './components/ProcessDetailView.js';
export { ProcessWidget, useProcessWidget } from './components/ProcessWidget.js';
export type { ProcessWidgetProps } from './components/ProcessWidget.js';

// Process state predicates
export {
  isLaunchable,
  isLaunchableState,
  isActive,
  isActiveState,
  isTerminal,
  isTerminalState,
  isWidgetLive,
  isWidgetLiveState,
  isCancellable,
  isCancellableState,
  isResumable,
} from './process-state.js';
export type { ProcessStateLike } from './process-state.js';
