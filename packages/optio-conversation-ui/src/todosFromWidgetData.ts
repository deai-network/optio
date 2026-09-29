import type { ConversationTodo } from './ConversationView.js';

const STATUSES = new Set(['pending', 'in_progress', 'completed', 'cancelled']);

/** The checklist on widgetData, or nothing when the payload is not one. */
export function todosFromWidgetData(widgetData: unknown): ConversationTodo[] {
  if (!widgetData || typeof widgetData !== 'object' || Array.isArray(widgetData)) return [];
  const raw = (widgetData as { todos?: unknown }).todos;
  if (!Array.isArray(raw)) return [];
  const out: ConversationTodo[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) continue;
    const row = entry as Record<string, unknown>;
    if (typeof row.text !== 'string' || row.text === '') continue;
    if (typeof row.status !== 'string' || !STATUSES.has(row.status)) continue;
    const id = typeof row.id === 'string' && row.id !== '' ? row.id : row.text;
    const todo: ConversationTodo = {
      id,
      text: row.text,
      status: row.status as ConversationTodo['status'],
    };
    if (typeof row.active === 'string') todo.active = row.active;
    out.push(todo);
  }
  return out;
}
