import { useCallback, useEffect, useState } from 'react';

// The selected process lives in the URL, /process/<id>, so a reload (or a
// shared link) keeps the selection. One route needs no router: this is the
// browser's history API. The server and Vite's dev server both answer an
// unknown path with index.html, so the app loads there and reads it back.
const PROCESS_PATH = '/process/';

/** The process id in a /process/<id> path, else null. */
export function processIdFromPath(pathname: string): string | null {
  if (!pathname.startsWith(PROCESS_PATH)) return null;
  const id = decodeURIComponent(pathname.slice(PROCESS_PATH.length).replace(/\/+$/, ''));
  return id === '' ? null : id;
}

/** The path that selects `id`; the root when nothing is selected. */
export function pathForProcess(id: string | null): string {
  return id === null ? '/' : `${PROCESS_PATH}${encodeURIComponent(id)}`;
}

/**
 * The selected process id, kept in the URL. Selecting pushes a history entry,
 * so the browser's back and forward step through earlier selections.
 */
export function useProcessRoute(): [string | null, (id: string | null) => void] {
  const [selected, setSelected] = useState(() => processIdFromPath(window.location.pathname));

  useEffect(() => {
    const onPopState = () => setSelected(processIdFromPath(window.location.pathname));
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const select = useCallback((id: string | null) => {
    const path = pathForProcess(id);
    if (path !== window.location.pathname) {
      window.history.pushState(null, '', path + window.location.search);
    }
    setSelected(id);
  }, []);

  return [selected, select];
}
