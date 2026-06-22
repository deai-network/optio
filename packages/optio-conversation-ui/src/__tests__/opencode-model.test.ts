import { describe, expect, it } from 'vitest';
import { parseProviders, lastModelFromHistory } from '../opencode/events.js';

// Shape verified against opencode 1.17.3-csillag.2 GET /config/providers:
// { providers: [{ id, name, models: { <modelId>: { id, providerID, name } } }],
//   default: { <providerID>: <modelId> } }
const PROVIDERS = {
  providers: [
    {
      id: 'opencode',
      name: 'OpenCode Zen',
      models: {
        'deepseek-v4-flash': { id: 'deepseek-v4-flash', providerID: 'opencode', name: 'DeepSeek V4 Flash' },
        'big-pickle': { id: 'big-pickle', providerID: 'opencode', name: 'Big Pickle' },
      },
    },
    {
      id: 'xai',
      name: 'xAI',
      models: { 'grok-5': { id: 'grok-5', providerID: 'xai', name: 'Grok 5' } },
    },
  ],
  default: { opencode: 'big-pickle', xai: 'grok-5' },
};

describe('parseProviders', () => {
  it('groups models by provider with id/name', () => {
    const { groups } = parseProviders(PROVIDERS);
    expect(groups.map((g) => g.providerName)).toEqual(['OpenCode Zen', 'xAI']);
    expect(groups[0].models).toEqual([
      { providerID: 'opencode', modelID: 'deepseek-v4-flash', label: 'DeepSeek V4 Flash' },
      { providerID: 'opencode', modelID: 'big-pickle', label: 'Big Pickle' },
    ]);
  });

  it('derives the default model from the first provider', () => {
    const { defaultModel } = parseProviders(PROVIDERS);
    expect(defaultModel).toEqual({ providerID: 'opencode', modelID: 'big-pickle' });
  });

  it('returns empty groups and null default for malformed input', () => {
    expect(parseProviders({})).toEqual({ groups: [], defaultModel: null });
    expect(parseProviders(null)).toEqual({ groups: [], defaultModel: null });
  });
});

describe('lastModelFromHistory', () => {
  it('returns the last assistant message model', () => {
    const history = [
      { info: { role: 'user' }, parts: [] },
      { info: { role: 'assistant', providerID: 'opencode', modelID: 'deepseek-v4-flash' }, parts: [] },
      { info: { role: 'assistant', providerID: 'xai', modelID: 'grok-5' }, parts: [] },
    ];
    expect(lastModelFromHistory(history)).toEqual({ providerID: 'xai', modelID: 'grok-5' });
  });

  it('returns null when no assistant message carries a model', () => {
    expect(lastModelFromHistory([{ info: { role: 'user' }, parts: [] }])).toBeNull();
    expect(lastModelFromHistory([])).toBeNull();
  });
});
