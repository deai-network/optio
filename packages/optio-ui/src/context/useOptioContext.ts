import { useContext } from 'react';
import { OptioContext } from './OptioProvider.js';
import type { OptioClient } from '../client.js';

export function useOptioPrefix(): string {
  return useContext(OptioContext).prefix;
}

export function useOptioBaseUrl(): string {
  return useContext(OptioContext).baseUrl;
}

export function useOptioClient(): OptioClient {
  return useContext(OptioContext).client;
}

export function useOptioDatabase(): string | undefined {
  return useContext(OptioContext).database;
}
