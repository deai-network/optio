import { createContext, useCallback, useContext, useState, useMemo, type CSSProperties, type ReactNode } from 'react';
import { Space } from 'antd';
import { useTranslation } from 'react-i18next';
import { WithSearch, useFilterFunction } from '@quaesitor-textus/core';
import {
  argOf, ArgStoreProvider, memoryStore, setArg, useArgWriter, useFieldContext, useStoredFields,
  type ArgStore, type FieldDefinition,
} from 'vultus-core';
import { Field } from 'vultus-antd';
import { toPredicate } from 'vultus-filtrum';
import type { Process, ProcessMetadataFilter } from 'optio-contracts';
import type { TaskFactoryOverrides } from '../task-factory-overrides.js';
import { ProcessList } from './ProcessList.js';

export type FilterGroup = 'all' | 'active' | 'hide_completed' | 'errors';

const QUIET_STATES = new Set(['idle', 'done']);

const NO_FILTERS: readonly FieldDefinition[] = [];

/** The width of each of an app's own filters in ProcessFilters' row, in pixels. */
const APP_FILTER_WIDTH = 240;

interface ProcessFilterContextValue {
  filterGroup: FilterGroup;
  setFilterGroup: (g: FilterGroup) => void;
  showDetails: boolean;
  setShowDetails: (v: boolean) => void;
  showSpecial: boolean;
  setShowSpecial: (v: boolean) => void;
  /** The app's own filters before the built-in ones (WithFilteredProcesses' `customFiltersBegin`), `[]` without. */
  customFiltersBegin: readonly FieldDefinition[];
  /** The app's own filters after the built-in ones (`customFiltersEnd`), `[]` without. */
  customFiltersEnd: readonly FieldDefinition[];
  /** Their values, by filter id: what was chosen, else the filter's default. */
  filterValues: Readonly<Record<string, unknown>>;
  setFilterValue: (id: string, value: unknown) => void;
  /**
   * The app filters' predicate over the process metadata (vultus-filtrum's `toPredicate`), for
   * `useProcessListStream({ metadataFilter })`; undefined without app filters or when they
   * filter nothing. It keeps its identity while it says the same.
   */
  metadataFilter: ProcessMetadataFilter | undefined;
  filterFn: (processes: any[]) => any[];
}

const ProcessFilterContext = createContext<ProcessFilterContextValue>(null as any);

export interface WithFilteredProcessesProps {
  children: ReactNode;
  /**
   * The app's own filters over the process metadata that ProcessFilters renders before its own:
   * vultus field definitions of the kinds vultus-filtrum gives a predicate for (`oneOf`,
   * `manyOf`, `oneOfTree`, `manyOfTree`), their ids unique across both slots. The predicate of
   * the two slots' filters together is `metadataFilter`.
   */
  customFiltersBegin?: readonly FieldDefinition[];
  /** The app's own filters that ProcessFilters renders after its own (as `customFiltersBegin`). */
  customFiltersEnd?: readonly FieldDefinition[];
  /** The metadata key of each filter whose id is not its key (filter id → key). */
  filterFields?: Readonly<Record<string, string>>;
  /**
   * Where every filter's value lives, under the filter's id (`search`, `filterGroup`,
   * `showDetails`, `showSpecial` and the app filters' ids; a value at its default is absent):
   * vultus-core's ArgStore, e.g. one over the URL, so a filtered list can be linked. A change
   * replaces the stored value (no history step). Without one, a memory store of its own, made
   * once per mount.
   */
  store?: ArgStore;
}

/** The built-in filters' definitions (their ids are their store keys), with their translated texts. */
type BuiltInDefinitions = {
  search: DefinitionOf<'text'>;
  filterGroup: DefinitionOf<'oneOf'>;
  showDetails: DefinitionOf<'boolean'>;
  showSpecial: DefinitionOf<'boolean'>;
};

const BuiltInContext = createContext<BuiltInDefinitions>(null as any);

function useBuiltInDefinitions(): BuiltInDefinitions {
  const { t } = useTranslation();
  return useMemo(() => ({
    search: {
      kind: 'text', id: 'search', label: t('processes.search'), placeholder: t('processes.search'),
    },
    filterGroup: {
      kind: 'oneOf',
      id: 'filterGroup',
      choices: [
        { value: 'all', label: t('processes.filterAll') },
        { value: 'active', label: t('processes.filterActive') },
        { value: 'hide_completed', label: t('processes.filterHideCompleted') },
        { value: 'errors', label: t('processes.filterErrors') },
      ],
    },
    showDetails: { kind: 'boolean', id: 'showDetails', label: t('processes.showDetails') },
    showSpecial: { kind: 'boolean', id: 'showSpecial', label: t('processes.showSpecial') },
  }), [t]);
}

/** The app filters' predicate over the values they have, kept stable by what it says. */
function useMetadataFilter(
  filters: readonly FieldDefinition[],
  filterValues: Readonly<Record<string, unknown>>,
  filterFields: Readonly<Record<string, string>> | undefined,
) {
  // The context the definitions' functions receive, as the fields below get it.
  const ctx = useFieldContext();
  const predicate = useMemo(() => {
    if (filters.length === 0) return undefined;
    const p = toPredicate(filters, filterValues, { ctx, fields: filterFields });
    // `{}` matches everything: no filter.
    return Object.keys(p).length === 0 ? undefined : p;
  }, [filters, filterValues, ctx, filterFields]);
  // The first predicate that said this, so that an app passing its definitions anew on each
  // render does not hand a new filter to the stream each time.
  const key = predicate === undefined ? '' : JSON.stringify(predicate);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on what the predicate says
  return useMemo(() => predicate, [key]);
}

function ProcessFilterInner({
  children, customFiltersBegin = NO_FILTERS, customFiltersEnd = NO_FILTERS, filterFields, builtIn,
}: WithFilteredProcessesProps & { builtIn: BuiltInDefinitions }) {
  const ctx = useFieldContext();
  const filters = useMemo(() => [...customFiltersBegin, ...customFiltersEnd], [customFiltersBegin, customFiltersEnd]);
  const definitions = useMemo(() => [...Object.values(builtIn), ...filters], [builtIn, filters]);
  // Every filter's value as its stored field shows it: the default where the store has no key.
  const { values } = useStoredFields(definitions);
  const writeArgs = useArgWriter();
  const setValue = useCallback((id: string, value: unknown) => {
    const definition = definitions.find((d) => d.id === id);
    if (!definition) throw new Error(`Filter '${id}': not a filter of this list`);
    writeArgs([setArg(argOf(definition, ctx), value)], 'replace');
  }, [definitions, writeArgs, ctx]);

  const filterGroup = values.filterGroup as FilterGroup;
  const showDetails = values.showDetails as boolean;
  const showSpecial = values.showSpecial as boolean;
  const setFilterGroup = useCallback((g: FilterGroup) => setValue('filterGroup', g), [setValue]);
  const setShowDetails = useCallback((v: boolean) => setValue('showDetails', v), [setValue]);
  const setShowSpecial = useCallback((v: boolean) => setValue('showSpecial', v), [setValue]);
  const filterValues = useMemo(
    () => Object.fromEntries(filters.map((definition) => [definition.id, values[definition.id]])),
    [filters, values],
  );
  const metadataFilter = useMetadataFilter(filters, filterValues, filterFields);

  const searchFilter = useFilterFunction();

  const filterFn = useMemo(
    () => (processes: any[]) => {
      const searched = processes.filter(searchFilter);
      return searched.filter((p) => {
        const state = p.status?.state;
        const isQuiet = QUIET_STATES.has(state) || !state;

        if (isQuiet && !showDetails && (p.depth ?? 0) !== 0) return false;
        if (isQuiet && !showSpecial && p.special === true) return false;

        if (filterGroup === 'active') return state !== 'idle' && state !== 'done';
        if (filterGroup === 'hide_completed') return state !== 'done';
        if (filterGroup === 'errors') return state === 'failed';
        return true;
      });
    },
    [searchFilter, filterGroup, showDetails, showSpecial],
  );

  return (
    <ProcessFilterContext.Provider
      value={{
        filterGroup, setFilterGroup, showDetails, setShowDetails, showSpecial, setShowSpecial,
        customFiltersBegin, customFiltersEnd, filterValues, setFilterValue: setValue, metadataFilter, filterFn,
      }}
    >
      {children}
    </ProcessFilterContext.Provider>
  );
}

/** Hands quaesitor-textus' search the query the store holds, and the store its changes. */
function StoredSearch({ children, ...rest }: WithFilteredProcessesProps) {
  const builtIn = useBuiltInDefinitions();
  const ctx = useFieldContext();
  const { values } = useStoredFields([builtIn.search]);
  const writeArgs = useArgWriter();
  const onSetQuery = useCallback(
    (query: string) => writeArgs([setArg(argOf(builtIn.search, ctx), query)], 'replace'),
    [writeArgs, builtIn, ctx],
  );
  return (
    <BuiltInContext.Provider value={builtIn}>
      <WithSearch fields={['name', 'description']} query={values.search as string} onSetQuery={onSetQuery}>
        <ProcessFilterInner {...rest} builtIn={builtIn}>{children}</ProcessFilterInner>
      </WithSearch>
    </BuiltInContext.Provider>
  );
}

/**
 * The filter state of a process list: the built-in filters (search, group, details, special),
 * applied by `filterFn` (FilteredProcessList), and the app's own filters over the metadata
 * (`customFiltersBegin`, `customFiltersEnd`), whose predicate (`useProcessFilter().metadataFilter`)
 * the app hands to its list stream. Every filter's value lives in `store` under the filter's id
 * (a value at its default is absent); without one, in a store of its own, gone on unmount.
 */
export function WithFilteredProcesses({ children, store, ...appFilters }: WithFilteredProcessesProps) {
  const [own] = useState(() => memoryStore());
  return (
    <ArgStoreProvider store={store ?? own}>
      <StoredSearch {...appFilters}>{children}</StoredSearch>
    </ArgStoreProvider>
  );
}

export function useProcessFilter(): ProcessFilterContextValue {
  return useContext(ProcessFilterContext);
}

type DefinitionOf<K extends FieldDefinition['kind']> = Extract<FieldDefinition, { kind: K }>;

/**
 * The filter controls of a process list, each a vultus-antd `Field` in `stored` mode (the value
 * is in the enclosing WithFilteredProcesses' store): the app's `customFiltersBegin`; the search, a
 * text field whose value is quaesitor-textus' query; the group, a one-of; "show details" and
 * "show special", booleans; the app's `customFiltersEnd`. An app filter is 240px wide.
 * `style`/`className` reach the row of controls (it keeps a 16px bottom margin unless the style
 * sets another), so a host app can place it in its own filter bar.
 */
export function ProcessFilters({ className, style }: { className?: string; style?: CSSProperties } = {}) {
  const { customFiltersBegin, customFiltersEnd } = useProcessFilter();
  const builtIn = useContext(BuiltInContext);

  const appFilter = (definition: FieldDefinition) => (
    <Field<FieldDefinition>
      key={definition.id}
      definition={definition}
      stored
      // A fixed width, as the built-in filters have: at its natural width a many-of's
      // responsive tags ("+N ...") and the select's width decide each other, and in this
      // wrapping row they never settle.
      style={{ width: APP_FILTER_WIDTH }}
    />
  );

  return (
    <Space size={[16, 8]} wrap className={className} style={{ marginBottom: 16, ...style }}>
      {customFiltersBegin.map(appFilter)}
      <Field definition={builtIn.search} stored widget="search" style={{ width: 200 }} />
      <Field definition={builtIn.filterGroup} stored style={{ width: 180 }} />
      <Field definition={builtIn.showDetails} stored widget="checkbox" />
      <Field definition={builtIn.showSpecial} stored widget="checkbox" />
      {customFiltersEnd.map(appFilter)}
    </Space>
  );
}

interface FilteredProcessListProps {
  processes: any[];
  loading: boolean;
  onLaunch?: (processId: string) => void;
  onLaunched?: (child: Process) => void;
  /** Overrides of a task factory's launch form, by the factory's `processId`. */
  launchOverrides?: Readonly<Record<string, TaskFactoryOverrides>>;
  onResurrect?: (processId: string) => void;
  onCancel?: (processId: string) => void;
  onProcessClick?: (processId: string) => void;
}

export function FilteredProcessList({ processes, ...rest }: FilteredProcessListProps) {
  const { filterFn } = useProcessFilter();
  return <ProcessList processes={filterFn(processes)} {...rest} />;
}
