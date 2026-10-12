import { createContext } from 'react';

/** The host app's callback for the agent's busy / idle state: true while the
 *  agent works on a turn, false when it is idle, the conversation closed, or
 *  the view gone. Called on every change (and once on mount). */
export type ConversationSetBusy = (busy: boolean) => void;

/** Set by ConversationWidget for the views under it; a host may provide it
 *  around the widgets it hosts. */
export const ConversationSetBusyContext = createContext<ConversationSetBusy | undefined>(undefined);
