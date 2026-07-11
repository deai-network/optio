import type { ReactNode } from 'react';
import { message as antdMessage } from 'antd';
import {
  MessageSinkContext, InternalLinkContext,
  type MessageSink, type InternalLinkComponent,
} from 'vultus-core';

const antdSink: MessageSink = (text) => { antdMessage.error(text); };

/**
 * Fully-wired vultus provider. Wrap your app once and every vultus-antd
 * component gets its dependency injection for free: unhandled action errors go
 * to antd's message.error, and internal (`/`-prefixed) markdown links render via
 * `linkComponent` when supplied (e.g. a react-router Link) — otherwise the
 * neutral plain-anchor default from vultus-core applies. The configurable
 * primitives (MessageSinkContext, InternalLinkContext, <Markdown>) remain
 * exported for advanced use.
 */
export function VultusProvider({
  children, linkComponent, messageSink = antdSink,
}: {
  children: ReactNode;
  linkComponent?: InternalLinkComponent;
  messageSink?: MessageSink;
}) {
  const wired = (
    <MessageSinkContext.Provider value={messageSink}>{children}</MessageSinkContext.Provider>
  );
  return linkComponent
    ? <InternalLinkContext.Provider value={linkComponent}>{wired}</InternalLinkContext.Provider>
    : wired;
}
