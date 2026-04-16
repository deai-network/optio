import { createContext, useContext, useState, useMemo, type ReactNode } from 'react';
import { Checkbox, Select, Space } from 'antd';
import { useTranslation } from 'react-i18next';
import { WithSearch, useSearchContext } from '@quaesitor-textus/core';
import { SearchInput } from '@quaesitor-textus/antd';
import { ProcessList } from './ProcessList.js';

export type FilterGroup = 'all' | 'active' | 'hide_completed' | 'errors';

const QUIET_STATES = new Set(['idle', 'done']);

function processSearchText(p: any): string {
  return [p.name, p.description].filter(Boolean).join(' ');
}

interface ProcessFilterContextValue {
  filterGroup: FilterGroup;
  setFilterGroup: (g: FilterGroup) => void;
  showDetails: boolean;
  setShowDetails: (v: boolean) => void;
  showSpecial: boolean;
  setShowSpecial: (v: boolean) => void;
  filterFn: (processes: any[]) => any[];
}

const ProcessFilterContext = createContext<ProcessFilterContextValue>(null as any);

function ProcessFilterInner({ children }: { children: ReactNode }) {
  const [filterGroup, setFilterGroup] = useState<FilterGroup>('all');
  const [showDetails, setShowDetails] = useState(false);
  const [showSpecial, setShowSpecial] = useState(false);

  const { filterFunction: searchFilter } = useSearchContext<any>({ mapping: processSearchText });

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
      value={{ filterGroup, setFilterGroup, showDetails, setShowDetails, showSpecial, setShowSpecial, filterFn }}
    >
      {children}
    </ProcessFilterContext.Provider>
  );
}

export function WithFilteredProcesses({ children }: { children: ReactNode }) {
  return (
    <WithSearch>
      <ProcessFilterInner>{children}</ProcessFilterInner>
    </WithSearch>
  );
}

export function useProcessFilter(): ProcessFilterContextValue {
  return useContext(ProcessFilterContext);
}

export function ProcessFilters() {
  const { filterGroup, setFilterGroup, showDetails, setShowDetails, showSpecial, setShowSpecial } = useProcessFilter();
  const { t } = useTranslation();

  return (
    <Space size={16} style={{ marginBottom: 16 }}>
      <SearchInput style={{ width: 200 }} placeholder={t('processes.search')} />
      <Select
        value={filterGroup}
        onChange={setFilterGroup}
        style={{ width: 180 }}
        options={[
          { value: 'all', label: t('processes.filterAll') },
          { value: 'active', label: t('processes.filterActive') },
          { value: 'hide_completed', label: t('processes.filterHideCompleted') },
          { value: 'errors', label: t('processes.filterErrors') },
        ]}
      />
      <Checkbox checked={showDetails} onChange={(e) => setShowDetails(e.target.checked)}>
        {t('processes.showDetails')}
      </Checkbox>
      <Checkbox checked={showSpecial} onChange={(e) => setShowSpecial(e.target.checked)}>
        {t('processes.showSpecial')}
      </Checkbox>
    </Space>
  );
}

interface FilteredProcessListProps {
  processes: any[];
  loading: boolean;
  onLaunch?: (processId: string) => void;
  onCancel?: (processId: string) => void;
  onProcessClick?: (processId: string) => void;
}

export function FilteredProcessList({ processes, ...rest }: FilteredProcessListProps) {
  const { filterFn } = useProcessFilter();
  return <ProcessList processes={filterFn(processes)} {...rest} />;
}
