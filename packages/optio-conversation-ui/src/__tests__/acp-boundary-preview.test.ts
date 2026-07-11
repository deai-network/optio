import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { initialChatState, type ChatItem, type ChatState } from '../chat.js';
import { reduceAcpEvent } from '../acp/events.js';

// Pins the two united-reducer fixes against BOTH hand-written shapes and REAL
// captured wire (kimi/grok/cursor browser SSE dumps → fixtures):
//   1. Tool boundary: a tool finalizes the answer bubble and PERSISTS as a row;
//      post-tool text opens a NEW bubble (no cross-tool merge, no vanishing tool
//      row — the "bubble-collapse" bug the ACP forks had, fixed per codex).
//   2. Content preview: when `toolCall.rawInput` is absent, the human-readable
//      detail is taken from `toolCall.content` text (kimi/cursor permission
//      cards + lazy tool_calls carry detail only there — the "empty card" bug).

function play(events: any[], from: ChatState = initialChatState): ChatState {
  return events.reduce((s, ev, i) => reduceAcpEvent(s, ev, i), from);
}

const chunk = (text: string) => ({
  jsonrpc: '2.0', method: 'session/update',
  params: { update: { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text } } },
});
const turnEnd = (id: number) => ({ jsonrpc: '2.0', id, result: { stopReason: 'end_turn' } });
const toolCall = (id: string, title: string, extra: Record<string, unknown> = {}) => ({
  jsonrpc: '2.0', method: 'session/update',
  params: { update: { sessionUpdate: 'tool_call', toolCallId: id, title, ...extra } },
});
const requestPermission = (id: number, toolCall: Record<string, unknown>) => ({
  jsonrpc: '2.0', id, method: 'session/request_permission',
  params: { toolCall, options: [{ optionId: 'a', kind: 'allow_once' }] },
});
const textContent = (t: string) => [{ type: 'content', content: { type: 'text', text: t } }];

const tools = (st: ChatState) => st.items.filter((i): i is Extract<ChatItem, { kind: 'tool' }> => i.kind === 'tool');
const assistants = (st: ChatState) =>
  st.items.filter((i): i is Extract<ChatItem, { kind: 'assistant' }> => i.kind === 'assistant');

describe('ACP reducer — tool boundary (bubble-collapse fix)', () => {
  it('a tool between messages persists AND splits the answer into two bubbles', () => {
    const st = play([chunk('Hello '), chunk('world'), toolCall('t1', 'Bash'), chunk('After tool')]);
    // Tool row survives the following message (was dropped before).
    expect(tools(st)).toHaveLength(1);
    // Two SEPARATE bubbles — pre-tool and post-tool text not merged.
    const a = assistants(st);
    expect(a.map((i) => i.text)).toEqual(['Hello world', 'After tool']);
    // Order: bubble, tool, bubble.
    expect(st.items.map((i) => i.kind)).toEqual(['assistant', 'tool', 'assistant']);
    // The pre-tool bubble is finalized (closed by the tool boundary).
    expect(a[0].pending).toBe(false);
  });

  it('tool rows persist across turn-end (they are history)', () => {
    const st = play([chunk('a'), toolCall('t1', 'Read'), turnEnd(1)]);
    expect(tools(st)).toHaveLength(1);
    expect(st.busy).toBe(false);
  });
});

describe('ACP reducer — content preview (empty-card fix)', () => {
  it('permission with no rawInput derives preview from content text', () => {
    const st = play([
      requestPermission(5, { title: 'Bash', content: textContent('Requesting approval to Running: echo hi') }),
    ]);
    const perm = st.items.find((i) => i.kind === 'permission') as Extract<ChatItem, { kind: 'permission' }>;
    expect(perm).toBeTruthy();
    expect(perm.toolName).toBe('Bash');
    expect(perm.input).toEqual({});
    expect(perm.preview).toBe('Requesting approval to Running: echo hi');
  });

  it('lazy tool_call with only content text exposes a preview', () => {
    const st = play([toolCall('t1', 'Read File', { content: textContent('{"path":"/x"}') })]);
    const t = tools(st)[0];
    expect(t.preview).toBe('{"path":"/x"}');
    expect(t.input).toEqual({});
  });

  it('rawInput object wins over content (KV table), preview cleared', () => {
    const st = play([toolCall('t1', 'Read', { rawInput: { path: '/x' }, content: textContent('ignored') })]);
    const t = tools(st)[0];
    expect(t.input).toEqual({ path: '/x' });
    expect(t.preview).toBeUndefined();
  });
});

// --- Real captured wire (browser SSE dumps → fixtures) ----------------------
const HERE = path.dirname(fileURLToPath(import.meta.url));
const fixture = (name: string) => path.join(HERE, 'fixtures', `${name}-acp-real.json`);
const load = (name: string): any[] | null => {
  const f = fixture(name);
  return fs.existsSync(f) ? (JSON.parse(fs.readFileSync(f, 'utf-8')) as any[]) : null;
};

describe('ACP reducer — real captured wire', () => {
  for (const agent of ['grok', 'grok-manual', 'kimi', 'cursor-manual', 'cursor-auto']) {
    const events = load(agent);
    it.skipIf(!events)(`${agent}: every tool_call yields a persistent tool row (no vanishing)`, () => {
      const toolIds = new Set(
        events!
          .filter((e) => e?.params?.update?.sessionUpdate === 'tool_call')
          .map((e) => String(e.params.update.toolCallId)),
      );
      const st = play(events!);
      // Fix invariant: no tool row is ever dropped, so the rendered tool rows
      // cover every distinct tool_call id seen on the wire.
      expect(toolIds.size).toBeGreaterThan(0);
      expect(tools(st).length).toBeGreaterThanOrEqual(toolIds.size);
    });
  }

  for (const agent of ['kimi', 'cursor-manual']) {
    const events = load(agent);
    it.skipIf(!events)(`${agent}: gated permission cards carry a content preview`, () => {
      const st = play(events!);
      const perms = st.items.filter(
        (i): i is Extract<ChatItem, { kind: 'permission' }> => i.kind === 'permission',
      );
      // These dumps were captured in manual mode → at least one real permission.
      expect(perms.length).toBeGreaterThan(0);
      // rawInput is absent on kimi/cursor permission toolCalls → preview is the
      // ONLY detail; every card must have one (else it renders name-only).
      for (const p of perms) expect(p.preview && p.preview.length > 0).toBe(true);
    });
  }

  // grok's gated permission is stock session/request_permission (same as
  // kimi/cursor — verified in the manual capture), but its toolCall DOES carry
  // rawInput, so the card renders the KV table (no preview needed). This pins
  // that grok needs no special-case permission handling.
  {
    const events = load('grok-manual');
    it.skipIf(!events)('grok-manual: gated permissions render from rawInput (no preview)', () => {
      const st = play(events!);
      const perms = st.items.filter(
        (i): i is Extract<ChatItem, { kind: 'permission' }> => i.kind === 'permission',
      );
      expect(perms.length).toBeGreaterThan(0);
      for (const p of perms) {
        expect(Object.keys(p.input as Record<string, unknown>).length).toBeGreaterThan(0);
      }
    });
  }
});
