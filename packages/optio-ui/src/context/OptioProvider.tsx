import { createContext, useMemo, type ReactNode } from 'react';
import { createOptioClient, type OptioClient } from '../client.js';

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

export function OptioProvider({ prefix = 'optio', baseUrl = '', children }: OptioProviderProps) {
  const client = useMemo(() => createOptioClient(baseUrl), [baseUrl]);

  return (
    <OptioContext.Provider value={{ prefix, baseUrl, client }}>
      {children}
    </OptioContext.Provider>
  );
}
