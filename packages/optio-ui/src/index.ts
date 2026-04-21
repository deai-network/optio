// Provider
export { OptioProvider } from './context/OptioProvider.js';
export { useOptioLive } from './context/useOptioContext.js';

// Components
export { ProcessList, ProcessItem } from './components/ProcessList.js';
export { ProcessStatusBadge } from './components/ProcessStatusBadge.js';
export { ProcessTreeView } from './components/ProcessTreeView.js';
export { ProcessLogPanel } from './components/ProcessLogPanel.js';
export { WithFilteredProcesses, ProcessFilters, FilteredProcessList, useProcessFilter } from './components/ProcessFilter.js';

// Hooks
export { useInstances, useInstanceDiscovery, type OptioInstance } from './hooks/useInstanceDiscovery.js';
export { useProcessActions } from './hooks/useProcessActions.js';
export { useProcessList, useProcess, useProcessTree, useProcessTreeLog } from './hooks/useProcessQueries.js';
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

// Components
export { ProcessDetailView } from './components/ProcessDetailView.js';
export type { ProcessDetailViewProps } from './components/ProcessDetailView.js';
