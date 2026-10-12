import { createContext } from 'react';
import type { LocalizedText } from './text.js';

/** An agent task's welcome card (optio-agents WelcomeCard, published by every
 *  engine as widgetData.welcome): a title and an optional markdown
 *  description, each a plain string or a language map (resolved in the
 *  widget); an optional icon URL relative to the web app root (normalized by
 *  the engine to start with "/"); and an optional minimum height of the icon
 *  in CSS pixels (the icon is otherwise as tall as the text). */
export interface ConversationWelcome {
  title: LocalizedText;
  description?: LocalizedText | null;
  icon?: string | null;
  minHeight?: number | null;
}

/** Set by ConversationWidget (and its suspended variant) from the process's
 *  widget data, for the views under it. */
export const ConversationWelcomeContext = createContext<ConversationWelcome | null>(null);

/** The welcome a process's widget data carries, if any. */
export function welcomeOf(process: unknown): ConversationWelcome | null {
  const welcome = (process as { widgetData?: { welcome?: unknown } } | undefined)?.widgetData?.welcome;
  if (!welcome || typeof welcome !== 'object') return null;
  const w = welcome as Partial<ConversationWelcome>;
  const titled = (typeof w.title === 'string' && w.title !== '')
    || (typeof w.title === 'object' && w.title !== null && Object.keys(w.title).length > 0);
  return titled ? (w as ConversationWelcome) : null;
}
