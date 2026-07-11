import { createContext, useContext } from 'react';
import type { ActionErrorCtx } from './types.js';

export const ActionErrorContext = createContext<ActionErrorCtx | null>(null);

export function useActionErrorCtx(): ActionErrorCtx | null {
  return useContext(ActionErrorContext);
}
