import React from 'react';
import { registerWidget, type WidgetProps } from 'optio-ui';
import { ClaudeCodeView } from './claudecode/ClaudeCodeView.js';
import { OpencodeView } from './opencode/OpencodeView.js';

/** Engine-neutral conversation widget: the task declares its wire protocol
 *  via widgetData.protocol; each view speaks that protocol natively through
 *  the widget proxy. */
export function ConversationWidget(props: WidgetProps) {
  const protocol = (props.process as any)?.widgetData?.protocol ?? 'claudecode';
  if (protocol === 'opencode') return <OpencodeView {...props} />;
  return <ClaudeCodeView {...props} />;
}

export function registerConversationWidget(): void {
  registerWidget('conversation', ConversationWidget);
  console.info('[optio-conversation-ui] conversation widget registered');
}
