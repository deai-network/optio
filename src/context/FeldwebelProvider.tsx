import { createContext, useMemo, type ReactNode } from 'react';
import { createFeldwebelClient, type FeldwebelClient } from '../client.js';

interface FeldwebelContextValue {
  prefix: string;
  baseUrl: string;
  client: FeldwebelClient;
}

export const FeldwebelContext = createContext<FeldwebelContextValue>(null as any);

interface FeldwebelProviderProps {
  prefix: string;
  baseUrl?: string;
  children: ReactNode;
}

export function FeldwebelProvider({ prefix, baseUrl = '', children }: FeldwebelProviderProps) {
  const client = useMemo(() => createFeldwebelClient(baseUrl), [baseUrl]);

  return (
    <FeldwebelContext.Provider value={{ prefix, baseUrl, client }}>
      {children}
    </FeldwebelContext.Provider>
  );
}
