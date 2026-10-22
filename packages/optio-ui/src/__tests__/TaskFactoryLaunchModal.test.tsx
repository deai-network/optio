import { describe, it, expect, vi, beforeAll, afterAll, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nextProvider } from 'react-i18next';
import i18next from 'i18next';
import { OptioProvider } from '../context/OptioProvider.js';
import { TaskFactoryLaunchModal } from '../components/TaskFactoryLaunchModal.js';
import type { TaskFactoryOverrides } from '../task-factory-overrides.js';

// No instance discovery requests: the provider is given its instance.
vi.mock('../hooks/useInstanceDiscovery.js', () => ({
  useInstanceDiscovery: () => ({
    instance: { database: 'test-db', prefix: 'test', live: true },
    instances: [{ database: 'test-db', prefix: 'test', live: true }],
    isLoading: false,
  }),
}));

// The UI speaks Hungarian; one launch refusal has its text, the rest show their keys.
const i18n = i18next.createInstance();
i18n.init({
  lng: 'hu',
  resources: {
    hu: { translation: { errors: { processes: { launch: { 'shutting-down': 'A motor leáll' } } } } },
  },
});

const FACTORY = {
  _id: 'f1',
  processId: 'drop-proposals',
  name: 'Drop proposals',
  status: { state: 'idle' },
  taskFactory: true,
  warning: 'This deletes the proposals it finds.',
  parameters: [
    { kind: 'boolean', id: 'dry_run', label: { en: 'Dry run', hu: 'Próbafutás' } },
    {
      kind: 'oneOf', id: 'mode', label: 'Mode', initialValue: 'slow',
      choices: [{ value: 'fast', label: 'Fast' }, { value: 'slow', label: { en: 'Slow', hu: 'Lassú' } }],
    },
    {
      kind: 'manyOf', id: 'sources', label: 'Sources', initialValue: ['deepl'],
      choices: [{ value: 'deepl', label: 'DeepL' }, { value: 'grok', label: 'Grok' }],
    },
    { kind: 'numeric', id: 'limit', label: 'Limit', min: 1, max: 100, integer: true, initialValue: 10 },
    { kind: 'range', id: 'units', label: 'Units', min: 1, max: 12, integer: true, initialValue: [2, 5] },
    { kind: 'text', id: 'note', label: 'Note', maxLength: 5, required: false },
  ],
};

const CHILD = { _id: 'c1', processId: 'drop-proposals:0a1b2c3d', name: 'Drop proposals' };

interface Answer { status: number; body: unknown }
let answer: Answer;
let requests: { url: string; method: string; body: any }[];

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function renderModal(
  process: any = FACTORY,
  host: { onKeyDown?: () => void; onClick?: () => void } = {},
  overrides?: TaskFactoryOverrides,
) {
  const onClose = vi.fn();
  const onLaunched = vi.fn();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={queryClient}>
        <OptioProvider prefix="test" database="test-db">
          {/* The host around the launch control (a process tree, a clickable row). */}
          <div onKeyDown={host.onKeyDown} onClick={host.onClick}>
            <TaskFactoryLaunchModal process={process} open onClose={onClose} onLaunched={onLaunched} overrides={overrides} />
          </div>
        </OptioProvider>
      </QueryClientProvider>
    </I18nextProvider>,
  );
  return { onClose, onLaunched, dialog: screen.getByRole('dialog') };
}

/** The form item (label, control, its messages) of the control named `name`. */
function itemOf(control: HTMLElement): HTMLElement {
  return control.closest('.ant-form-item') as HTMLElement;
}

const launchButton = () => screen.getByRole('button', { name: 'Launch' });

async function launchedWith(): Promise<Record<string, unknown>> {
  await waitFor(() => expect(requests).toHaveLength(1), { timeout: 60_000 });
  return requests[0].body.params;
}

describe('TaskFactoryLaunchModal', () => {
  // antd's modal locks body scroll and measures the scrollbar via
  // getComputedStyle(el, '::-webkit-scrollbar'); jsdom logs "Not implemented"
  // for the pseudo-element, so drop the argument (same result).
  beforeAll(() => {
    const real = globalThis.getComputedStyle.bind(globalThis);
    vi.spyOn(globalThis, 'getComputedStyle').mockImplementation((elt) => real(elt));
  });
  afterAll(() => {
    vi.restoreAllMocks();
  });

  beforeEach(() => {
    answer = { status: 200, body: CHILD };
    requests = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      requests.push({
        url: String(input),
        method: init?.method ?? 'GET',
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      return json(answer.status, answer.body);
    });
  });

  afterEach(() => {
    cleanup();
  });

  it("shows the factory's warning on top, then a field per parameter, labelled in the UI language", () => {
    const { dialog } = renderModal();
    const warning = within(dialog).getByText(FACTORY.warning);
    const fields = [
      within(dialog).getByRole('switch', { name: 'Próbafutás' }),
      within(dialog).getByRole('combobox', { name: 'Mode' }),
      within(dialog).getByRole('checkbox', { name: 'DeepL' }),
      within(dialog).getByRole('spinbutton', { name: 'Limit' }),
      within(dialog).getAllByRole('slider')[0],
      within(dialog).getByRole('textbox', { name: 'Note' }),
    ];
    for (const field of fields) {
      expect(warning.compareDocumentPosition(field) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    // A choice's label is resolved too: the select shows the Hungarian one.
    expect(within(itemOf(fields[1])).getByText('Lassú')).toBeTruthy();
  });

  it('starts from the initial values', () => {
    const { dialog } = renderModal();
    expect(within(dialog).getByRole('switch', { name: 'Próbafutás' }).getAttribute('aria-checked')).toBe('false');
    expect((within(dialog).getByRole('checkbox', { name: 'DeepL' }) as HTMLInputElement).checked).toBe(true);
    expect((within(dialog).getByRole('checkbox', { name: 'Grok' }) as HTMLInputElement).checked).toBe(false);
    expect((within(dialog).getByRole('spinbutton', { name: 'Limit' }) as HTMLInputElement).value).toBe('10');
    expect(within(dialog).getAllByRole('slider').map((s) => s.getAttribute('aria-valuenow'))).toEqual(['2', '5']);
    expect((within(dialog).getByRole('textbox', { name: 'Note' }) as HTMLInputElement).value).toBe('');
  });

  it('launches the factory with the values, then closes and hands over the launched child', async () => {
    const { onClose, onLaunched } = renderModal();
    fireEvent.click(launchButton());
    expect(await launchedWith()).toEqual({
      dry_run: false, mode: 'slow', sources: ['deepl'], limit: 10, units: [2, 5], note: '',
    });
    expect(requests[0].method).toBe('POST');
    expect(requests[0].url).toContain('/api/processes/f1/launch');
    expect(typeof requests[0].body.sessionId).toBe('string');
    await waitFor(() => expect(onLaunched).toHaveBeenCalledWith(CHILD), { timeout: 60_000 });
    expect(onClose).toHaveBeenCalled();
  });

  it('launches with the values entered', async () => {
    const { dialog } = renderModal();
    fireEvent.click(within(dialog).getByRole('switch', { name: 'Próbafutás' }));
    fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Mode' }));
    const fast = await waitFor(() => {
      const option = screen.getAllByText('Fast').find((el) => el.closest('.ant-select-item-option'));
      expect(option).toBeTruthy();
      return option!;
    }, { timeout: 60_000 });
    fireEvent.click(fast);
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Grok' }));
    const limit = within(dialog).getByRole('spinbutton', { name: 'Limit' });
    fireEvent.change(limit, { target: { value: '20' } });
    fireEvent.blur(limit);
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Note' }), { target: { value: 'abc' } });
    fireEvent.click(launchButton());
    expect(await launchedWith()).toEqual({
      dry_run: true, mode: 'fast', sources: ['deepl', 'grok'], limit: 20, units: [2, 5], note: 'abc',
    });
  });

  it("shows an invalid-parameters refusal on the field its message names, and keeps the values", async () => {
    answer = { status: 400, body: { reason: 'invalid-parameters', message: 'note: must not be "hello"' } };
    const { dialog, onClose, onLaunched } = renderModal();
    const note = within(dialog).getByRole('textbox', { name: 'Note' });
    fireEvent.change(note, { target: { value: 'hello' } });
    fireEvent.click(launchButton());
    await launchedWith();
    await waitFor(
      () => expect(within(itemOf(note)).getByText('must not be "hello"')).toBeTruthy(),
      { timeout: 60_000 },
    );
    expect((within(dialog).getByRole('textbox', { name: 'Note' }) as HTMLInputElement).value).toBe('hello');
    expect((within(dialog).getByRole('checkbox', { name: 'DeepL' }) as HTMLInputElement).checked).toBe(true);
    expect(onLaunched).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    // The other fields are not marked.
    expect(within(itemOf(within(dialog).getByRole('spinbutton', { name: 'Limit' }))).queryByRole('alert')).toBeNull();
  });

  it('shows a refusal naming no field of the form, with its own message, in the form', async () => {
    answer = { status: 400, body: { reason: 'invalid-parameters', message: 'Pick fewer sources for this many units' } };
    const { dialog, onClose } = renderModal();
    fireEvent.click(launchButton());
    await launchedWith();
    expect(await within(dialog).findByText('Pick fewer sources for this many units', {}, { timeout: 60_000 }))
      .toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("shows the other refusals in the form, with their reason's text", async () => {
    answer = { status: 409, body: { reason: 'shutting-down', message: 'The engine is shutting down' } };
    const { dialog, onClose } = renderModal();
    fireEvent.click(launchButton());
    await launchedWith();
    expect(await within(dialog).findByText('A motor leáll', {}, { timeout: 60_000 })).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('refuses, before launching, a required parameter left empty and a text over its maxLength', async () => {
    const factory = {
      ...FACTORY,
      warning: undefined,
      parameters: [
        { kind: 'text', id: 'title', label: 'Title' },
        { kind: 'manyOf', id: 'tags', label: 'Tags', choices: [{ value: 'x', label: 'X' }] },
        { kind: 'text', id: 'note', label: 'Note', maxLength: 5, required: false },
      ],
    };
    const { dialog } = renderModal(factory);
    const note = within(dialog).getByRole('textbox', { name: 'Note' });
    fireEvent.change(note, { target: { value: 'too long' } });
    fireEvent.click(launchButton());
    await waitFor(() => {
      expect(within(itemOf(within(dialog).getByRole('textbox', { name: 'Title' }))).getByText('Required')).toBeTruthy();
      expect(within(itemOf(within(dialog).getByRole('checkbox', { name: 'X' }))).getByText('Required')).toBeTruthy();
      expect(within(itemOf(note)).getByText('At most 5 characters')).toBeTruthy();
    }, { timeout: 60_000 });
    expect(requests).toHaveLength(0);
  });

  it('has the ephemeral checkbox last, checked, and sends it apart from the params', async () => {
    const { dialog } = renderModal();
    const ephemeral = within(dialog).getByRole('checkbox', { name: 'Remove when finished' }) as HTMLInputElement;
    expect(ephemeral.checked).toBe(true);
    expect(within(dialog).queryByRole('switch', { name: 'Remove when finished' })).toBeNull();
    // The label appears once, and the help reaches the user as the control's accessible description.
    expect(within(dialog).getAllByText('Remove when finished')).toHaveLength(1);
    expect(ephemeral.getAttribute('aria-description')).toMatch(/^Checked: the launch disappears/);
    const note = within(dialog).getByRole('textbox', { name: 'Note' });
    expect(note.compareDocumentPosition(ephemeral) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(launchButton());
    expect(await launchedWith()).not.toHaveProperty('ephemeral');
    expect(requests[0].body.ephemeral).toBe(true);
  });

  it('sends ephemeral false when it is unchecked', async () => {
    const { dialog } = renderModal();
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Remove when finished' }));
    fireEvent.click(launchButton());
    await launchedWith();
    expect(requests[0].body.ephemeral).toBe(false);
  });

  it('starts the checkbox unchecked for a factory with a TTL, and says how long it keeps', () => {
    const { dialog } = renderModal({ ...FACTORY, ttlSeconds: 3600 });
    const ephemeral = within(dialog).getByRole('checkbox', { name: 'Remove when finished' }) as HTMLInputElement;
    expect(ephemeral.checked).toBe(false);
    expect(within(dialog).getAllByText('Remove when finished')).toHaveLength(1);
    expect(ephemeral.getAttribute('aria-description')).toMatch(/at most 1 hour after it ends\.$/);
  });

  it('a hidden field is not shown and its value is sent', async () => {
    const { dialog } = renderModal(FACTORY, {}, { dry_run: { visible: false, initialValue: false } });
    expect(within(dialog).queryByRole('switch', { name: 'Próbafutás' })).toBeNull();
    // The whole item, label included, is antd's hidden one (display: none), its value kept in the form.
    const label = within(dialog).getByText('Próbafutás');
    expect(itemOf(label).classList.contains('ant-form-item-hidden')).toBe(true);
    expect(getComputedStyle(itemOf(label)).display).toBe('none');
    expect(within(dialog).getByRole('combobox', { name: 'Mode' })).toBeTruthy();
    fireEvent.click(launchButton());
    expect((await launchedWith()).dry_run).toBe(false);
  });

  it('resolves an overridden label, initial value and a function of the field context', async () => {
    const { dialog } = renderModal(FACTORY, {}, {
      limit: { label: () => 'Cap', initialValue: 42 },
      note: { hidden: () => true },
    });
    expect((within(dialog).getByRole('spinbutton', { name: 'Cap' }) as HTMLInputElement).value).toBe('42');
    expect(within(dialog).queryByRole('textbox', { name: 'Note' })).toBeNull();
    fireEvent.click(launchButton());
    expect((await launchedWith()).limit).toBe(42);
  });

  it('shows a refusal naming a hidden field with its label in the form', async () => {
    answer = { status: 400, body: { reason: 'invalid-parameters', message: 'dry_run: must be true or false' } };
    const { dialog, onClose } = renderModal(FACTORY, {}, { dry_run: { visible: false, initialValue: false } });
    fireEvent.click(launchButton());
    await launchedWith();
    expect(await within(dialog).findByText('Próbafutás: must be true or false', {}, { timeout: 60_000 }))
      .toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('fails loudly, at render, on an override of a field the form lacks', () => {
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => renderModal(FACTORY, {}, { nope: { visible: false } }))
      .toThrow("Task factory overrides: no field 'nope' in the form");
    quiet.mockRestore();
  });

  it("keeps its events from the host's handlers around it", () => {
    // React carries a portal's events up its own tree: without the boundary, a
    // process tree's keyboard navigation would take Home, End and the arrows.
    const onKeyDown = vi.fn();
    const onClick = vi.fn();
    const { dialog } = renderModal(FACTORY, { onKeyDown, onClick });
    const note = within(dialog).getByRole('textbox', { name: 'Note' });
    fireEvent.keyDown(note, { key: 'Home' });
    fireEvent.keyDown(note, { key: 'ArrowDown' });
    fireEvent.click(note);
    expect(onKeyDown).not.toHaveBeenCalled();
    expect(onClick).not.toHaveBeenCalled();
  });

  it('closes on Cancel without launching', async () => {
    const { onClose } = renderModal();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(onClose).toHaveBeenCalled(), { timeout: 60_000 });
    expect(requests).toHaveLength(0);
  });
});
