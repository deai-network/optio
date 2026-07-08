// packages/optio-agents-ui/src/__tests__/agents.test.ts
import { describe, it, expect } from 'vitest';
import { AGENTS, getAgentInfo, type AgentType } from '../index.js';

const EXPECTED: Record<AgentType, { name: string; url: string }> = {
  antigravity: { name: 'Antigravity CLI', url: 'https://antigravity.google' },
  claudecode: { name: 'Claude Code', url: 'https://claude.com/product/claude-code' },
  codex: { name: 'Codex', url: 'https://openai.com/codex' },
  cursor: { name: 'Cursor CLI', url: 'https://cursor.com/cli' },
  grok: { name: 'Grok Build', url: 'https://x.ai/cli' },
  kimicode: { name: 'Kimi Code', url: 'https://www.kimi.com/coding' },
  opencode: { name: 'OpenCode', url: 'https://opencode.ai' },
};

describe('AGENTS catalog', () => {
  it('has exactly the 7 canonical engines', () => {
    expect(Object.keys(AGENTS).sort()).toEqual(Object.keys(EXPECTED).sort());
  });

  it('exposes the canonical name and url for each engine', () => {
    for (const slug of Object.keys(EXPECTED) as AgentType[]) {
      expect(getAgentInfo(slug)).toEqual({ slug, ...EXPECTED[slug] });
    }
  });
});
