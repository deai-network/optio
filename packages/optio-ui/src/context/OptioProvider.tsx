import { createContext, useMemo, type ReactNode } from 'react';
import { createOptioClient, type OptioClient } from '../client.js';
import { usePrefixDiscovery } from '../hooks/usePrefixDiscovery.js';

interface OptioContextValue {
  prefix: string;
  baseUrl: string;
  client: OptioClient;
}

export const OptioContext = createContext<OptioContextValue>(null as any);

interface OptioProviderProps {
  prefix?: string;
  baseUrl?: string;
  children: ReactNode;
}

function OptioProviderInner({ explicitPrefix, baseUrl, client, children }: {
  explicitPrefix: string | undefined;
  baseUrl: string;
  client: OptioClient;
  children: ReactNode;
}) {
  const { prefix: discoveredPrefix } = usePrefixDiscovery();
  const prefix = explicitPrefix ?? discoveredPrefix ?? 'optio';

  return (
    <OptioContext.Provider value={{ prefix, baseUrl, client }}>
      {children}
    </OptioContext.Provider>
  );
}

export function OptioProvider({ prefix, baseUrl = '', children }: OptioProviderProps) {
  const client = useMemo(() => createOptioClient(baseUrl), [baseUrl]);

  return (
    <OptioContext.Provider value={{ prefix: prefix ?? 'optio', baseUrl, client }}>
      <OptioProviderInner explicitPrefix={prefix} baseUrl={baseUrl} client={client}>
        {children}
      </OptioProviderInner>
    </OptioContext.Provider>
  );
}
