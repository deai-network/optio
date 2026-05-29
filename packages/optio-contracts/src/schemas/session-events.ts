import { z } from 'zod';
import { SessionEventSchema } from './process.js';

/**
 * Wire shape of one message on the GET /api/session-events/stream SSE feed.
 * The poller emits one `session-events` message per tick carrying the new
 * sessionEvents of every process whose originatingSessionId matches the
 * subscriber's sessionId. `processId` is the process _id hex (string).
 */
export const SessionEventsStreamMessageSchema = z.object({
  type: z.literal('session-events'),
  processId: z.string(),
  events: z.array(SessionEventSchema),
});

export type SessionEventsStreamMessage = z.infer<typeof SessionEventsStreamMessageSchema>;
