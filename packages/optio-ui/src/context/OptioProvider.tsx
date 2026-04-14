import { createContext, useMemo, type ReactNode } from 'react';
import { createOptioClient, type OptioClient } from '../client.js';
import { useInstanceDiscovery } from '../hooks/useInstanceDiscovery.js';

interface OptioContextValue {
  prefix: string;
  database: string | undefined;
  baseUrl: string;
  client: OptioClient;
}

export const OptioContext = createContext<OptioContextValue>(null as any);

interface OptioProviderProps {
  prefix?: string;
  database?: string;
  baseUrl?: string;
  children: ReactNode;
}

function OptioProviderInner({ explicitPrefix, explicitDatabase, baseUrl, client, children }: {
  explicitPrefix: string | undefined;
  explicitDatabase: string | undefined;
  baseUrl: string;
  client: OptioClient;
  children: ReactNode;
}) {
  const { instance: discoveredInstance } = useInstanceDiscovery();
  const prefix = explicitPrefix ?? discoveredInstance?.prefix ?? 'optio';
  const database = explicitDatabase ?? discoveredInstance?.database;

  return (
    <OptioContext.Provider value={{ prefix, database, baseUrl, client }}>
      {children}
    </OptioContext.Provider>
  );
}

export function OptioProvider({ prefix, database, baseUrl = '', children }: OptioProviderProps) {
  const client = useMemo(() => createOptioClient(baseUrl), [baseUrl]);

  return (
    <OptioContext.Provider value={{ prefix: prefix ?? 'optio', database, baseUrl, client }}>
      <OptioProviderInner explicitPrefix={prefix} explicitDatabase={database} baseUrl={baseUrl} client={client}>
        {children}
      </OptioProviderInner>
    </OptioContext.Provider>
  );
}
