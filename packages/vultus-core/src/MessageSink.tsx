import { createContext, useContext } from 'react';

/**
 * How the action framework surfaces an otherwise-unhandled error (no form field,
 * no inline error target). Default is a no-op so vultus-core stays antd-free and
 * framework-neutral; vultus-antd's VultusProvider wires this to antd's
 * message.error. Hosts may inject any toast/notification mechanism.
 */
export type MessageSink = (text: string) => void;

export const MessageSinkContext = createContext<MessageSink>(() => {});

export function useMessageSink(): MessageSink {
  return useContext(MessageSinkContext);
}
