import { useContext } from 'react';
import { FeldwebelContext } from './FeldwebelProvider.js';
import type { FeldwebelClient } from '../client.js';

export function useFeldwebelPrefix(): string {
  return useContext(FeldwebelContext).prefix;
}

export function useFeldwebelBaseUrl(): string {
  return useContext(FeldwebelContext).baseUrl;
}

export function useFeldwebelClient(): FeldwebelClient {
  return useContext(FeldwebelContext).client;
}
