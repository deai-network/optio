import { Checkbox, Select, Space } from 'antd';
import { useTranslation } from 'react-i18next';

export type FilterGroup = 'all' | 'active' | 'hide_completed' | 'errors';

interface ProcessFiltersProps {
  filterGroup: FilterGroup;
  onFilterChange: (group: FilterGroup) => void;
  showDetails: boolean;
  onShowDetailsChange: (show: boolean) => void;
  showSpecial: boolean;
  onShowSpecialChange: (show: boolean) => void;
}

export function ProcessFilters(props: ProcessFiltersProps) {
  const { t } = useTranslation();

  return (
    <Space size={16} style={{ marginBottom: 16 }}>
      <Select
        value={props.filterGroup}
        onChange={props.onFilterChange}
        style={{ width: 180 }}
        options={[
          { value: 'all', label: t('processes.filterAll') },
          { value: 'active', label: t('processes.filterActive') },
          { value: 'hide_completed', label: t('processes.filterHideCompleted') },
          { value: 'errors', label: t('processes.filterErrors') },
        ]}
      />
      <Checkbox checked={props.showDetails} onChange={(e) => props.onShowDetailsChange(e.target.checked)}>
        {t('processes.showDetails')}
      </Checkbox>
      <Checkbox checked={props.showSpecial} onChange={(e) => props.onShowSpecialChange(e.target.checked)}>
        {t('processes.showSpecial')}
      </Checkbox>
    </Space>
  );
}
