import { describe, it, expect } from 'vitest';
import { todosFromWidgetData } from '../todosFromWidgetData.js';

const write = { id: '1', text: 'Write', status: 'in_progress', active: 'Writing' };

describe('todosFromWidgetData', () => {
  it('reads a valid checklist and keeps the active phrase', () => {
    expect(todosFromWidgetData({
      protocol: 'grok',
      uploadUrl: '/up',
      todos: [
        write,
        { id: '2', text: 'Ship', status: 'pending' },
      ],
    })).toEqual([
      write,
      { id: '2', text: 'Ship', status: 'pending' },
    ]);
  });

  it('returns nothing when widget data has no list', () => {
    expect(todosFromWidgetData(undefined)).toEqual([]);
    expect(todosFromWidgetData(null)).toEqual([]);
    expect(todosFromWidgetData('nope')).toEqual([]);
    expect(todosFromWidgetData({ protocol: 'grok' })).toEqual([]);
    expect(todosFromWidgetData({ todos: 'nope' })).toEqual([]);
  });

  it('keeps a description and ignores a blank one', () => {
    expect(todosFromWidgetData({
      todos: [
        { id: '10', text: 'Get the source', status: 'pending', description: 'Ask where it lives.' },
        { id: '11', text: 'No detail', status: 'pending', description: '   ' },
        { id: '12', text: 'Weird', status: 'pending', description: 4 },
      ],
    })).toEqual([
      { id: '10', text: 'Get the source', status: 'pending', description: 'Ask where it lives.' },
      { id: '11', text: 'No detail', status: 'pending' },
      { id: '12', text: 'Weird', status: 'pending' },
    ]);
  });

  it('drops entries that are not a checklist row', () => {
    expect(todosFromWidgetData({
      todos: [
        { text: 'Write', status: 'pending' },
        { id: 'x', status: 'pending' },
        { id: 'y', text: 'Weird', status: 'started' },
        null,
        { id: '', text: 'Blank id', status: 'completed' },
      ],
    })).toEqual([
      { id: 'Write', text: 'Write', status: 'pending' },
      { id: 'Blank id', text: 'Blank id', status: 'completed' },
    ]);
  });
});
