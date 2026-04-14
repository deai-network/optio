import { createAuthClient } from 'better-auth/react';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const authClient = createAuthClient({
  baseURL: window.location.origin,
}) as any; // Suppress "inferred type cannot be named" TS2742 — better-auth ships non-portable path types

export const { useSession, signOut } = authClient;
