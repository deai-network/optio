import { createContext } from 'react';

/** The conversation's reading width, the host's choice: 'wide' always uses the
 *  full width, 'narrow' always the capped reading column (the default), and
 *  'switch' shows the Wide switch in the conversation's header. */
export type ConversationWideness = 'wide' | 'narrow' | 'switch';

/** Set by ConversationWidget for the views under it (live and suspended). */
export const ConversationWidenessContext = createContext<ConversationWideness>('narrow');
