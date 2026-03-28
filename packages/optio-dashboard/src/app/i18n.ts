import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

i18n.use(initReactI18next).init({
  lng: 'en',
  resources: {
    en: {
      translation: {
        'processes.launch': 'Launch',
        'processes.cancel': 'Cancel',
        'processes.filterAll': 'All',
        'processes.filterActive': 'Active',
        'processes.filterHideCompleted': 'Hide completed',
        'processes.filterErrors': 'Errors',
        'processes.showDetails': 'Show details',
        'processes.showSpecial': 'Show special',
        'status.idle': 'Idle',
        'status.scheduled': 'Scheduled',
        'status.running': 'Running',
        'status.done': 'Done',
        'status.failed': 'Failed',
        'status.cancel_requested': 'Cancel requested',
        'status.cancelling': 'Cancelling',
        'status.cancelled': 'Cancelled',
        'common.noData': 'No data',
      },
    },
  },
  interpolation: { escapeValue: false },
});

export default i18n;
