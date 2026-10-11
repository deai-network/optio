import { createContext, useContext, useState, type ReactNode } from 'react';

// Ant Design X experiment only (never for main): the demo dashboard renders
// either inside daeo-style's DaeoStyleProvider (the vultus bridge, as excavator
// does: Daeo's antd theme, DaeoSpinner lent through vultus's spinner slot) or
// plainly (optio's own antd look, the conversation widget owning its
// light/dark). The choice is kept in localStorage (`?style=daeo|plain` sets
// it); switching reloads, so daeo-style's stylesheet loads only in the Daeo
// look.

export type StyleMode = 'daeo' | 'plain';

const STYLE_KEY = 'optio-dashboard.style';
const DAEO_THEME_KEY = 'optio-dashboard.daeoTheme';

export const styleMode: StyleMode = (() => {
  const asked = new URLSearchParams(window.location.search).get('style');
  if (asked === 'daeo' || asked === 'plain') {
    localStorage.setItem(STYLE_KEY, asked);
    return asked;
  }
  return localStorage.getItem(STYLE_KEY) === 'plain' ? 'plain' : 'daeo';
})();

export function switchStyleMode(mode: StyleMode): void {
  if (mode === styleMode) return;
  localStorage.setItem(STYLE_KEY, mode);
  const url = new URL(window.location.href);
  url.searchParams.delete('style');
  window.location.replace(url.toString());
}

type DaeoTheme = 'light' | 'dark';

/** The Daeo look's light/dark, for the header's toggle; null in the plain look. */
export const DaeoThemeContext = createContext<{ theme: DaeoTheme; setTheme: (t: DaeoTheme) => void } | null>(null);

export function useDaeoTheme() {
  return useContext(DaeoThemeContext);
}

type DaeoProvider = (props: { theme: DaeoTheme; children: ReactNode }) => ReactNode;

/** The app inside daeo-style's provider, its light/dark kept in localStorage. */
export function DaeoRoot({ Provider, children }: { Provider: DaeoProvider; children: ReactNode }) {
  const [theme, setThemeState] = useState<DaeoTheme>(
    () => (localStorage.getItem(DAEO_THEME_KEY) === 'dark' ? 'dark' : 'light'),
  );
  const setTheme = (t: DaeoTheme) => {
    localStorage.setItem(DAEO_THEME_KEY, t);
    setThemeState(t);
  };
  return (
    <DaeoThemeContext.Provider value={{ theme, setTheme }}>
      <Provider theme={theme}>{children}</Provider>
    </DaeoThemeContext.Provider>
  );
}
