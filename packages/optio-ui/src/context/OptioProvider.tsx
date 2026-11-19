import { createContext, useMemo, useEffect, type ReactNode } from 'react';
import { notification } from 'antd';
import { createOptioClient, type OptioClient } from '../client.js';
import { useInstanceDiscovery } from '../hooks/useInstanceDiscovery.js';
import { startSessionEvents, resetSession, type SessionEventCallbacks } from '../session/sessionEvents.js';
import { setBrowserOpenHandler, setBrowserOpenNotifier, type BrowserOpenHandler } from '../handlers/browserOpen.js';

interface OptioContextValue {
  prefix: string;
  database: string | undefined;
  live: boolean;
  baseUrl: string;
  client: OptioClient;
  resetSession: () => void;
}

export const OptioContext = createContext<OptioContextValue>(null as any);

// The context value, the same object while its fields stay the same: the
// instances poll re-renders the provider at each fetch's start and end, and a
// new value object would re-render every consumer with it.
function useOptioContextValue(prefix: string, database: string | undefined, live: boolean, baseUrl: string, client: OptioClient): OptioContextValue {
  return useMemo(() => ({ prefix, database, live, baseUrl, client, resetSession }), [prefix, database, live, baseUrl, client]);
}

interface OptioProviderProps {
  prefix?: string;
  database?: string;
  live?: boolean;
  baseUrl?: string;
  onAttention?: (processId: string, reason: string) => void;
  onClientMessage?: (processId: string, keyword: string, data: unknown) => void;
  // Override the browser-open handler (agent OAuth "open this URL" requests).
  // undefined -> optio-ui's default (window.open + notification fallback).
  onBrowserOpen?: BrowserOpenHandler;
  children: ReactNode;
}

function OptioProviderInner({ explicitPrefix, explicitDatabase, explicitLive, baseUrl, client, children }: {
  explicitPrefix: string | undefined;
  explicitDatabase: string | undefined;
  explicitLive: boolean | undefined;
  baseUrl: string;
  client: OptioClient;
  children: ReactNode;
}) {
  const { instance: discoveredInstance } = useInstanceDiscovery();
  const prefix = explicitPrefix ?? discoveredInstance?.prefix ?? 'optio';
  const database = explicitDatabase ?? discoveredInstance?.database;
  const live = explicitLive ?? discoveredInstance?.live ?? false;
  const value = useOptioContextValue(prefix, database, live, baseUrl, client);

  return (
    <OptioContext.Provider value={value}>
      {children}
    </OptioContext.Provider>
  );
}

export function OptioProvider({ prefix, database, live, baseUrl = '', onAttention, onClientMessage, onBrowserOpen, children }: OptioProviderProps) {
  const client = useMemo(() => createOptioClient(baseUrl), [baseUrl]);

  // Mount the always-on session-events manager once. Re-runs when the
  // callbacks or baseUrl change; startSessionEvents updates callbacks in
  // place and only (re)connects on baseUrl change.
  useEffect(() => {
    const callbacks: SessionEventCallbacks = { onAttention, onClientMessage };
    startSessionEvents(baseUrl, prefix, database, callbacks);
  }, [baseUrl, prefix, database, onAttention, onClientMessage]);

  // Install the browser-open handler override (undefined restores the default).
  // Module-level, mirroring how sessionEvents holds onAttention/onClientMessage.
  useEffect(() => {
    setBrowserOpenHandler(onBrowserOpen);
    return () => setBrowserOpenHandler(undefined);
  }, [onBrowserOpen]);

  // The default handler's popup-blocked notification renders through this
  // hook instance (inside the app's tree, so under its ConfigProvider) rather
  // than antd's static notification.
  const [notificationApi, notificationHolder] = notification.useNotification();
  useEffect(() => {
    setBrowserOpenNotifier(notificationApi);
    return () => setBrowserOpenNotifier(undefined);
  }, [notificationApi]);

  const value = useOptioContextValue(prefix ?? 'optio', database, live ?? false, baseUrl, client);

  return (
    <OptioContext.Provider value={value}>
      {notificationHolder}
      <OptioProviderInner explicitPrefix={prefix} explicitDatabase={database} explicitLive={live} baseUrl={baseUrl} client={client}>
        {children}
      </OptioProviderInner>
    </OptioContext.Provider>
  );
}
