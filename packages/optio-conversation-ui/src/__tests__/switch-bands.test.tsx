import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import huHU from 'antd/locale/hu_HU';
import type { ReactElement } from 'react';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';
import { ClaudeCodeView } from '../claudecode/ClaudeCodeView.js';
import { ClaudeCodeReplayView } from '../claudecode/ClaudeCodeReplayView.js';
import type { ChatItem, ChatState, CustomControl, SessionControl } from '../chat.js';

// Switch bands (owner request 2026-10-12): a model switch, and a confirmed
// pick of a task-defined control, as the band the resume band uses; the
// resume band names the mode the resumed run runs in.

const SPEED: CustomControl = {
  id: 'speed',
  label: null,
  description: {
    en: 'What should be the balance between detail level and speed?',
    hu: 'Mi legyen az egyensúly a részletesség és a gyorsaság között?',
  },
  options: [
    { value: 'fast', label: { en: 'Fast', hu: 'Gyors' }, description: { en: 'Give me a quick answer' } },
    { value: 'extensive', label: { en: 'Extensive', hu: 'Alapos' }, description: null },
  ],
  value: 'extensive',
};

const TONE: CustomControl = {
  id: 'tone',
  label: 'Tone',
  options: [{ value: 'concise', label: 'Concise' }, { value: 'detailed', label: 'Detailed' }],
  value: 'concise',
};

const MODEL_CONTROL: SessionControl[] = [
  { id: 'model', kind: 'select', label: 'Model', value: 'claude-opus-5',
    options: [{ value: 'claude-sonnet-5-5', label: 'Sonnet 5.5' }, { value: 'claude-opus-5', label: 'Opus 5' }] },
];

function makeState(items: ChatItem[]): ChatState {
  return { items, busy: false, closed: false, controls: [] };
}

function makeProps(over: Partial<ConversationViewProps> = {}): ConversationViewProps {
  return {
    state: makeState([]),
    closed: false,
    busy: false,
    toolVerbosity: 'verbose',
    thinkingVerbosity: 'visible',
    showFileUpload: false,
    maxUploadBytes: 10_000_000,
    fileDownload: false,
    onSend: vi.fn(async () => true),
    onInterrupt: vi.fn(),
    onPermission: vi.fn(),
    onFileDownload: vi.fn(),
    ...over,
  };
}

function renderView(props: ConversationViewProps, locale?: typeof huHU) {
  return render(<ConfigProvider locale={locale}>{(<ConversationView {...props} />) as ReactElement}</ConfigProvider>);
}

const bandTexts = () => screen.queryAllByTestId('switch-band').map((b) => b.textContent);

describe('switch band texts', () => {
  it('a model switch is a band in the resume band look: "Model switched." without the model control', () => {
    renderView(makeProps({ state: makeState([
      { kind: 'resumed', seq: 1 },
      { kind: 'switched', seq: 2, model: 'claude-sonnet-5-5' },
    ]) }));
    const band = screen.getByTestId('switch-band');
    const resume = screen.getByTestId('resume-band');
    expect(band.textContent).toBe('Model switched.');
    // The resume marker's look: a solid X Bubble.Divider.
    const divider = band.querySelector('.ant-bubble-divider .ant-divider');
    expect(divider).not.toBeNull();
    expect(divider!.classList.contains('ant-divider-dashed')).toBe(false);
    expect(divider!.className).toBe(resume.querySelector('.ant-bubble-divider .ant-divider')!.className);
  });

  it('"Model switched to <label>." when the built-in model control is shown, else its id', () => {
    renderView(makeProps({
      controls: MODEL_CONTROL, onControlChange: vi.fn(),
      state: makeState([
        { kind: 'switched', seq: 1, model: 'claude-sonnet-5-5' },
        { kind: 'switched', seq: 2, model: 'claude-haiku-5' },
        { kind: 'switched', seq: 3 },
      ]),
    }));
    expect(bandTexts()).toEqual(['Model switched to Sonnet 5.5.', 'Model switched to claude-haiku-5.', 'Model switched.']);
  });

  it('"Model switched." when the controls shown have no model control', () => {
    renderView(makeProps({
      controls: [{ id: 'thinking', kind: 'segmented', label: 'Thinking', value: 'low', levels: ['low', 'high'] }],
      onControlChange: vi.fn(),
      state: makeState([{ kind: 'switched', seq: 1, model: 'claude-sonnet-5-5' }]),
    }));
    expect(bandTexts()).toEqual(['Model switched.']);
  });

  it('our pick: "Switched to <option label> mode.", lowercased', () => {
    renderView(makeProps({
      customControls: [SPEED, TONE], onCustomControlChange: vi.fn(async () => null),
      state: makeState([
        { kind: 'switched', seq: 1, control: 'speed', option: 'fast', model: 'claude-sonnet-5-5' },
        { kind: 'switched', seq: 2, control: 'tone', option: 'detailed' },
      ]),
    }));
    expect(bandTexts()).toEqual(['Switched to fast mode.', 'Switched to detailed mode.']);
  });

  it('the option label in the UI language', () => {
    renderView(makeProps({
      customControls: [SPEED], onCustomControlChange: vi.fn(async () => null),
      state: makeState([{ kind: 'switched', seq: 1, control: 'speed', option: 'fast' }]),
    }), huHU);
    expect(bandTexts()).toEqual(['Switched to gyors mode.']);
  });

  it('an unknown control or option falls back to the generic text', () => {
    renderView(makeProps({
      controls: MODEL_CONTROL, onControlChange: vi.fn(),
      customControls: [SPEED], onCustomControlChange: vi.fn(async () => null),
      state: makeState([
        { kind: 'switched', seq: 1, control: 'speed', option: 'turbo', model: 'claude-sonnet-5-5' },
        { kind: 'switched', seq: 2, control: 'gone', option: 'fast' },
      ]),
    }));
    expect(bandTexts()).toEqual(['Model switched to Sonnet 5.5.', 'Model switched.']);
  });
});

describe('resume band naming the mode', () => {
  const resumeText = () => screen.getByTestId('resume-band').textContent;
  const withControls = { customControls: [SPEED, TONE], onCustomControlChange: vi.fn(async () => null) };

  it('"Session has been saved and resumed in <label> mode."', () => {
    renderView(makeProps({ ...withControls, state: makeState([
      { kind: 'resumed', seq: 1, options: [{ control: 'speed', option: 'extensive' }] },
    ]) }));
    expect(resumeText()).toBe('Session has been saved and resumed in extensive mode.');
  });

  it('several controls: the labels joined with ", "', () => {
    renderView(makeProps({ ...withControls, state: makeState([
      { kind: 'resumed', seq: 1, options: [{ control: 'speed', option: 'fast' }, { control: 'tone', option: 'concise' }] },
    ]) }));
    expect(resumeText()).toBe('Session has been saved and resumed in fast, concise mode.');
  });

  it('in the UI language', () => {
    renderView(makeProps({ ...withControls, state: makeState([
      { kind: 'resumed', seq: 1, options: [{ control: 'speed', option: 'extensive' }] },
    ]) }), huHU);
    expect(resumeText()).toBe('Session has been saved and resumed in alapos mode.');
  });

  it.each([
    ['no recorded options', undefined],
    ['an unknown option', [{ control: 'speed', option: 'turbo' }]],
  ])('the text as before with %s', (_, options) => {
    renderView(makeProps({ ...withControls, state: makeState([{ kind: 'resumed', seq: 1, ...(options ? { options } : {}) }]) }));
    expect(resumeText()).toBe('Session has been saved and resumed.');
  });
});

// The whole widget, fed the listener's SSE frames.
class MockEventSource {
  static last: MockEventSource | null = null;
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  constructor(url: string) {
    this.url = url;
    MockEventSource.last = this;
  }
  close() {}
  emit(ev: unknown, seq: number) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(ev), lastEventId: String(seq) }));
  }
}

function widgetProps(widgetData: any) {
  return {
    process: { _id: 'p1', name: 'n', widgetData, status: { state: 'running' } },
    apiBaseUrl: '',
    widgetProxyUrl: '/api/widget/db/gm/p1/',
    prefix: 'gm',
    database: 'db',
  } as any;
}

let seq = 0;
function fire(...events: unknown[]) {
  for (const ev of events) act(() => MockEventSource.last!.emit(ev, ++seq));
}

const T = '2026-10-11T21:41:05.230Z';
const turn = [
  { type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'q' }] } },
  { type: 'assistant', message: { role: 'assistant', id: 'msg_1', content: [{ type: 'text', text: 'a' }] } },
  { type: 'result', subtype: 'success', result: 'a' },
];
// Claude Code 2.1.296's echo of a model switch, as captured.
const echo = (model: string) => ({
  type: 'user', message: { role: 'user', content: `<local-command-stdout>Set model to \`${model}\`</local-command-stdout>` },
  session_id: 's1', parent_tool_use_id: null, uuid: `echo-${model}`, timestamp: T, isReplay: true,
});
const pick = (value: string) => ({ type: 'x-optio-custom-pick', id: 'speed', value });
const controlResponse = { type: 'control_response', response: { subtype: 'success', request_id: 'req_1', response: {} } };
const update = (value: string | null, controls: SessionControl[] = []) =>
  ({ type: 'x-optio-control-update', controls, customControls: [{ ...SPEED, value }] });

const idle = () => screen.getByTestId('custom-control-speed').querySelector('.ant-segmented-disabled') === null;

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  (globalThis as any).EventSource = MockEventSource as any;
  MockEventSource.last = null;
  seq = 0;
});

describe('ClaudeCodeView switch bands', () => {
  it('a pick, live: exactly one band naming the option, no echo bubble, the chat stays idle', async () => {
    let answer!: (r: Response) => void;
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => { answer = resolve; }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ClaudeCodeView {...widgetProps({ customControls: [SPEED] })} />);
    fire(...turn);
    fireEvent.click(screen.getByText('Fast'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    fire(pick('fast'), echo('claude-sonnet-5-5'), controlResponse, update('fast'));
    await act(async () => answer(new Response(JSON.stringify({ ok: true }), { status: 200 })));
    expect(bandTexts()).toEqual(['Switched to fast mode.']);
    expect(screen.queryByText(/local-command-stdout|Set model to/)).toBeNull();
    await waitFor(() => expect(idle()).toBe(true));
  });

  it('a pick that changes only the effort draws its band too', () => {
    render(<ClaudeCodeView {...widgetProps({ customControls: [SPEED] })} />);
    fire(...turn, pick('fast'), controlResponse, update('fast'));
    expect(bandTexts()).toEqual(['Switched to fast mode.']);
  });

  it('a refused pick draws no band', () => {
    render(<ClaudeCodeView {...widgetProps({ customControls: [SPEED] })} />);
    fire(...turn, pick('fast'), update('extensive'));
    expect(bandTexts()).toEqual([]);
    expect(idle()).toBe(true);
  });

  it('a switch optio did not ask for names the model when the built-in model control is shown', () => {
    render(<ClaudeCodeView {...widgetProps({ showSessionControls: true, controls: MODEL_CONTROL, customControls: [SPEED] })} />);
    fire(...turn, echo('claude-sonnet-5-5'));
    expect(bandTexts()).toEqual(['Model switched to Sonnet 5.5.']);
    expect(idle()).toBe(true);
  });

  it('a switch optio did not ask for, without the built-in controls: "Model switched."', () => {
    render(<ClaudeCodeView {...widgetProps({ controls: MODEL_CONTROL, customControls: [SPEED] })} />);
    fire(...turn, echo('claude-sonnet-5-5'));
    expect(bandTexts()).toEqual(['Model switched.']);
  });

  it('a replayed buffer after a resume shows the same bands, and the resume band names the mode', () => {
    render(<ClaudeCodeView {...widgetProps({ customControls: [{ ...SPEED, value: 'fast' }] })} />);
    fire(
      ...turn,
      pick('fast'), echo('claude-sonnet-5-5'), controlResponse, update('fast'),
      echo('claude-opus-5'),
      { type: 'x-optio-resumed', customControls: [{ id: 'speed', value: 'fast' }] },
      { type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'System: you have been resumed' }] } },
    );
    expect(bandTexts()).toEqual(['Switched to fast mode.', 'Model switched.']);
    const resume = screen.getByTestId('resume-band');
    expect(resume.textContent).toBe('Session has been saved and resumed in fast mode.');
    const [, generic] = screen.getAllByTestId('switch-band');
    expect(generic.compareDocumentPosition(resume) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

// The suspended replay (ClaudeCodeReplayView) folds the saved record through
// the same reducer, read-only: its bands name the task-defined modes as the
// live view's do, and no selector is offered.
describe('ClaudeCodeReplayView switch bands', () => {
  function replay(widgetData: any, events: unknown[]) {
    return render(
      <ClaudeCodeReplayView
        process={{ _id: 'p1', name: 'n', widgetData, status: { state: 'cancelled' } } as any}
        widgetData={widgetData}
        events={events.map((ev, i) => [i + 1, ev])}
        capturedAt={null}
      />,
    );
  }

  it('a pick in the record: "Switched to fast mode.", and no selector renders', () => {
    replay({ protocol: 'claudecode', customControls: [SPEED] }, [
      ...turn,
      pick('fast'), echo('claude-sonnet-5-5'), controlResponse, update('fast'),
      echo('claude-opus-5'),
    ]);
    expect(bandTexts()).toEqual(['Switched to fast mode.', 'Model switched.']);
    expect(screen.queryByText(/local-command-stdout|Set model to/)).toBeNull();
    expect(screen.queryByTestId('custom-control-speed')).toBeNull();
    expect(document.querySelector('.ant-segmented')).toBeNull();
  });

  it('the resume band names the mode from widgetData\'s customControls', () => {
    replay({ protocol: 'claudecode', customControls: [SPEED] }, [
      ...turn,
      { type: 'x-optio-resumed', customControls: [{ id: 'speed', value: 'fast' }] },
    ]);
    expect(screen.getByTestId('resume-band').textContent).toBe('Session has been saved and resumed in fast mode.');
    expect(screen.queryByTestId('custom-control-speed')).toBeNull();
  });
});
