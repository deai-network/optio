import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

i18n.use(initReactI18next).init({
  lng: 'en',
  resources: {
    en: {
      translation: {
        'processes.launch': 'Launch',
        'processes.cancel': 'Cancel',
        'processes.suspend': 'Suspend',
        'processes.search': 'Search',
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
        'status.suspending': 'Suspending',
        'status.suspended': 'Suspended',
        'common.noData': 'No data',
        // A task factory's launch refused (optio-ui's optioErrorRoutes, route processes.launch).
        'errors.processes.launch.not-found': 'This process no longer exists.',
        'errors.processes.launch.not-launchable': 'This process cannot be launched now.',
        'errors.processes.launch.no-resume-support': 'This task cannot resume.',
        'errors.processes.launch.launch-blocked': 'Launches of this task are blocked for now.',
        'errors.processes.launch.shutting-down': 'The engine is shutting down. Try again once it is back.',
        'errors.processes.launch.parameters-required': 'This task needs the values of its parameters.',
        'errors.processes.launch.not-a-factory': 'This task takes no parameters.',
        'errors.processes.launch.invalid-parameters': 'A value was refused: see its field.',
      },
    },
  },
  interpolation: { escapeValue: false },
});

export default i18n;
