import { createContext, useCallback, useContext, useState, useMemo, type CSSProperties, type ReactNode } from 'react';
import { Space } from 'antd';
import { useTranslation } from 'react-i18next';
import { WithSearch, useFilterFunction, useSearchContext } from '@quaesitor-textus/core';
import { initialValueOf, useFieldContext, type FieldDefinition } from 'vultus-core';
import { Field, type FieldProps } from 'vultus-antd';
import { toPredicate } from 'vultus-filtrum';
import type { Process, ProcessMetadataFilter } from 'optio-contracts';
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
  /** The app's own filters (WithFilteredProcesses' `filters`), `[]` without. */
  filters: readonly FieldDefinition[];
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
   * The app's own filters over the process metadata: vultus field definitions of the kinds
   * vultus-filtrum gives a predicate for (`oneOf`, `manyOf`, `oneOfTree`, `manyOfTree`).
   * ProcessFilters renders them after its own; their predicate is `metadataFilter`.
   */
  filters?: readonly FieldDefinition[];
  /** The metadata key of each filter whose id is not its key (filter id → key). */
  filterFields?: Readonly<Record<string, string>>;
}

/**
 * The app filters' values: the ones chosen, else each filter's default (its `initialValue`,
 * else its kind's own, as vultus-core's `initialValueOf` gives it), and their predicate.
 */
function useMetadataFilters(filters: readonly FieldDefinition[], filterFields: Readonly<Record<string, string>> | undefined) {
  // The context the definitions' functions receive, as the fields below get it.
  const ctx = useFieldContext();
  const [chosen, setChosen] = useState<Readonly<Record<string, unknown>>>({});
  const filterValues = useMemo(
    () => Object.fromEntries(filters.map((definition) => [
      definition.id,
      Object.hasOwn(chosen, definition.id) ? chosen[definition.id] : initialValueOf(definition, ctx),
    ])),
    [filters, chosen, ctx],
  );
  const setFilterValue = useCallback((id: string, value: unknown) => setChosen((c) => ({ ...c, [id]: value })), []);
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
  const metadataFilter = useMemo(() => predicate, [key]);
  return { filterValues, setFilterValue, metadataFilter };
}

function ProcessFilterInner({ children, filters = NO_FILTERS, filterFields }: WithFilteredProcessesProps) {
  const [filterGroup, setFilterGroup] = useState<FilterGroup>('all');
  const [showDetails, setShowDetails] = useState(false);
  const [showSpecial, setShowSpecial] = useState(false);
  const { filterValues, setFilterValue, metadataFilter } = useMetadataFilters(filters, filterFields);

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
        filters, filterValues, setFilterValue, metadataFilter, filterFn,
      }}
    >
      {children}
    </ProcessFilterContext.Provider>
  );
}

/**
 * The filter state of a process list: the built-in filters (search, group, details, special),
 * applied by `filterFn` (FilteredProcessList), and the app's own `filters` over the metadata,
 * whose predicate (`useProcessFilter().metadataFilter`) the app hands to its list stream.
 */
export function WithFilteredProcesses({ children, ...appFilters }: WithFilteredProcessesProps) {
  return (
    <WithSearch fields={['name', 'description']}>
      <ProcessFilterInner {...appFilters}>{children}</ProcessFilterInner>
    </WithSearch>
  );
}

export function useProcessFilter(): ProcessFilterContextValue {
  return useContext(ProcessFilterContext);
}

type DefinitionOf<K extends FieldDefinition['kind']> = Extract<FieldDefinition, { kind: K }>;

/**
 * The filter controls of a process list, each a vultus-antd `Field` (local mode: the value is
 * the enclosing WithFilteredProcesses' state): the search, a text field whose value is
 * quaesitor-textus' query; the group, a one-of; "show details" and "show special", booleans;
 * then the app's own filters, each 240px wide. `style`/`className` reach the row of controls (it keeps a 16px
 * bottom margin unless the style sets another), so a host app can place it in its own filter bar.
 */
export function ProcessFilters({ className, style }: { className?: string; style?: CSSProperties } = {}) {
  const {
    filterGroup, setFilterGroup, showDetails, setShowDetails, showSpecial, setShowSpecial,
    filters, filterValues, setFilterValue,
  } = useProcessFilter();
  const { query, setQuery } = useSearchContext();
  const { t } = useTranslation();

  const builtIn = useMemo(() => ({
    search: {
      kind: 'text', id: 'search', label: t('processes.search'), placeholder: t('processes.search'),
    } satisfies DefinitionOf<'text'>,
    filterGroup: {
      kind: 'oneOf',
      id: 'filterGroup',
      choices: [
        { value: 'all', label: t('processes.filterAll') },
        { value: 'active', label: t('processes.filterActive') },
        { value: 'hide_completed', label: t('processes.filterHideCompleted') },
        { value: 'errors', label: t('processes.filterErrors') },
      ],
    } satisfies DefinitionOf<'oneOf'>,
    showDetails: { kind: 'boolean', id: 'showDetails', label: t('processes.showDetails') } satisfies DefinitionOf<'boolean'>,
    showSpecial: { kind: 'boolean', id: 'showSpecial', label: t('processes.showSpecial') } satisfies DefinitionOf<'boolean'>,
  }), [t]);

  return (
    <Space size={[16, 8]} wrap className={className} style={{ marginBottom: 16, ...style }}>
      <Field definition={builtIn.search} widget="search" value={query} onChange={setQuery} style={{ width: 200 }} />
      <Field
        definition={builtIn.filterGroup}
        value={filterGroup}
        onChange={(group) => setFilterGroup(group as FilterGroup)}
        style={{ width: 180 }}
      />
      <Field definition={builtIn.showDetails} widget="checkbox" value={showDetails} onChange={setShowDetails} />
      <Field definition={builtIn.showSpecial} widget="checkbox" value={showSpecial} onChange={setShowSpecial} />
      {filters.map((definition) => (
        <Field<FieldDefinition>
          key={definition.id}
          definition={definition}
          // A value the field itself asked for (or the definition's default), so it fits the kind.
          value={filterValues[definition.id] as FieldProps['value']}
          onChange={(value) => setFilterValue(definition.id, value)}
          // A fixed width, as the built-in filters have: at its natural width a many-of's
          // responsive tags ("+N ...") and the select's width decide each other, and in this
          // wrapping row they never settle.
          style={{ width: APP_FILTER_WIDTH }}
        />
      ))}
    </Space>
  );
}

interface FilteredProcessListProps {
  processes: any[];
  loading: boolean;
  onLaunch?: (processId: string) => void;
  onLaunched?: (child: Process) => void;
  onResurrect?: (processId: string) => void;
  onCancel?: (processId: string) => void;
  onProcessClick?: (processId: string) => void;
}

export function FilteredProcessList({ processes, ...rest }: FilteredProcessListProps) {
  const { filterFn } = useProcessFilter();
  return <ProcessList processes={filterFn(processes)} {...rest} />;
}
