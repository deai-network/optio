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
        // An error with neither a reason nor a message (vultus-core's routeApiError).
        'common.error': 'Something went wrong.',
        // A launch refused (optio-ui's optioErrorRoutes, route processes.launch): in a task
        // factory's form, else a message (App's messageSink).
        'errors.processes.launch.not-found': 'This process no longer exists.',
        'errors.processes.launch.not-launchable': 'This process cannot be launched now.',
        'errors.processes.launch.no-resume-support': 'This task cannot resume.',
        'errors.processes.launch.launch-blocked': 'Launches of this task are blocked for now.',
        'errors.processes.launch.shutting-down': 'The engine is shutting down. Try again once it is back.',
        'errors.processes.launch.parameters-required': 'This task needs the values of its parameters.',
        'errors.processes.launch.not-a-factory': 'This task takes no parameters.',
        'errors.processes.launch.invalid-parameters': 'A value was refused: see its field.',
        'errors.processes.launch.invalid-metadata': 'The server could not stamp this launch: its stamp collides with the task\'s metadata.',
        'errors.processes.launch.invalid-request': 'A resume from an older snapshot needs resume: true.',
        'errors.processes.launch.no-snapshot-support': 'This task keeps no snapshot history.',
        'errors.processes.launch.snapshot-not-found': 'That snapshot no longer exists. Open the menu again for the current list.',
        'errors.processes.launch.capture-pending': 'An unfinished snapshot capture of this process must be settled first: Resurrect, or a plain Resume.',
        // A removal of an ended launch refused (route processes.dismiss).
        'errors.processes.dismiss.not-found': 'This launch no longer exists.',
        'errors.processes.dismiss.not-dismissable': 'This launch cannot be removed now.',
        // A resurrect refused (route processes.resurrect).
        'errors.processes.resurrect.not-found': 'This process no longer exists.',
        'errors.processes.resurrect.not-resurrectable': 'There is no unsaved work to save for this process now.',
        'errors.processes.resurrect.no-resurrect-support': 'This task cannot save the work a failed run left behind.',
        'errors.processes.resurrect.resurrect-in-progress': 'A resurrect of this process is already running.',
        'errors.processes.resurrect.launch-blocked': 'Launches of this task are blocked for now.',
        'errors.processes.resurrect.shutting-down': 'The engine is shutting down. Try again once it is back.',
        'errors.processes.resurrect.not-launchable': 'Another launch of this process is in progress. Try again shortly.',
      },
    },
  },
  interpolation: { escapeValue: false },
});

export default i18n;
