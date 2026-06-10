import { createContext, useMemo, useEffect, type ReactNode } from 'react';
import { createOptioClient, type OptioClient } from '../client.js';
import { useInstanceDiscovery } from '../hooks/useInstanceDiscovery.js';
import { startSessionEvents, resetSession, type SessionEventCallbacks } from '../session/sessionEvents.js';

interface OptioContextValue {
  prefix: string;
  database: string | undefined;
  live: boolean;
  baseUrl: string;
  client: OptioClient;
  resetSession: () => void;
}

export const OptioContext = createContext<OptioContextValue>(null as any);

interface OptioProviderProps {
  prefix?: string;
  database?: string;
  live?: boolean;
  baseUrl?: string;
  onAttention?: (processId: string, reason: string) => void;
  onClientMessage?: (processId: string, keyword: string, data: unknown) => void;
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

  return (
    <OptioContext.Provider value={{ prefix, database, live, baseUrl, client, resetSession }}>
      {children}
    </OptioContext.Provider>
  );
}

export function OptioProvider({ prefix, database, live, baseUrl = '', onAttention, onClientMessage, children }: OptioProviderProps) {
  const client = useMemo(() => createOptioClient(baseUrl), [baseUrl]);

  // Mount the always-on session-events manager once. Re-runs when the
  // callbacks or baseUrl change; startSessionEvents updates callbacks in
  // place and only (re)connects on baseUrl change.
  useEffect(() => {
    const callbacks: SessionEventCallbacks = { onAttention, onClientMessage };
    startSessionEvents(baseUrl, prefix, database, callbacks);
  }, [baseUrl, prefix, database, onAttention, onClientMessage]);

  return (
    <OptioContext.Provider value={{ prefix: prefix ?? 'optio', database, live: live ?? false, baseUrl, client, resetSession }}>
      <OptioProviderInner explicitPrefix={prefix} explicitDatabase={database} explicitLive={live} baseUrl={baseUrl} client={client}>
        {children}
      </OptioProviderInner>
    </OptioContext.Provider>
  );
}
