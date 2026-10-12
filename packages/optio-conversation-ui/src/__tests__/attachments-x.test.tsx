import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';

// The paperclip's file input: X Attachments' picker, inside the button's antd
// Upload (its drop area holds a second, separate file input).
const pickerInput = () =>
  screen.getByTestId('attach-button').closest('.ant-upload')!.querySelector('input[type="file"]') as HTMLInputElement;

// Ant Design X experiment (owner-approved 2026-10-12): files to send are
// collected by X Attachments. The paperclip opens X's picker, picked files show as X attachment cards (FileCards
// with name and size, a remove button), and a file pasted into the message box
// is attached too. The files still travel as before: onSend(text, attachments).

function view(over: Partial<ConversationViewProps> = {}) {
  const onSend = vi.fn(async () => true);
  const props: ConversationViewProps = {
    state: { items: [], busy: false, closed: false, controls: [] },
    closed: false, busy: false, toolVerbosity: 'verbose', thinkingVerbosity: 'visible',
    showFileUpload: true, maxUploadBytes: 1000, fileDownload: false,
    onSend, onInterrupt: vi.fn(), onPermission: vi.fn(), onFileDownload: vi.fn(),
    ...over,
  };
  const utils = render(<ConversationView {...props} />);
  return { ...utils, onSend: props.onSend as typeof onSend };
}

const note = (name = 'note.txt', bytes = 2) => new File([new Uint8Array(bytes)], name, { type: 'text/plain' });
const cardNames = () => [...document.querySelectorAll('[data-testid="attach-chips"] .ant-attachment-list .ant-file-card-file-name')]
  .map((n) => n.textContent);

async function pick(files: File[]) {
  await act(async () => {
    fireEvent.change(pickerInput(), { target: { files } });
  });
}

async function send(text: string) {
  fireEvent.change(screen.getByTestId('conversation-input-box'), { target: { value: text } });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  });
}

describe('X Attachments', () => {
  it('a picked file shows as an X attachment card; Send passes it on, then the list empties', async () => {
    const { onSend } = view();
    expect(screen.queryByTestId('attach-chips')).toBeNull();
    await pick([note('note.txt')]);
    await waitFor(() => expect(cardNames()).toEqual(['note.txt']));

    await send('summarize this');
    expect(onSend).toHaveBeenCalledTimes(1);
    const [text, attachments] = onSend.mock.calls[0] as unknown as [string, { filename: string; mime: string; file: File }[]];
    expect(text).toBe('summarize this');
    expect(attachments.map((a) => [a.filename, a.mime])).toEqual([['note.txt', 'text/plain']]);
    expect(attachments[0].file).toBeInstanceOf(File);
    await waitFor(() => expect(screen.queryByTestId('attach-chips')).toBeNull());
  });

  it("the card's remove button drops the file", async () => {
    const { onSend } = view();
    await pick([note('a.txt'), note('b.txt')]);
    await waitFor(() => expect(cardNames()).toEqual(['a.txt', 'b.txt']));
    const removes = document.querySelectorAll('[data-testid="attach-chips"] .ant-file-card-list-remove');
    await act(async () => {
      fireEvent.click(removes[0]);
    });
    // (In jsdom the removed card stays mounted in its leave motion, so the
    // files that remain are read from what Send passes on.)
    await send('go');
    const [, attachments] = onSend.mock.calls[0] as unknown as [string, { filename: string }[]];
    expect(attachments.map((a) => a.filename)).toEqual(['b.txt']);
  });

  it('a file pasted into the message box is attached', async () => {
    view();
    const box = screen.getByTestId('conversation-input-box');
    await act(async () => {
      fireEvent.paste(box, { clipboardData: { files: [note('pasted.txt')], getData: () => '' } });
    });
    await waitFor(() => expect(cardNames()).toEqual(['pasted.txt']));
  });

  it('a file over the size cap is refused with "File too large."', async () => {
    view({ maxUploadBytes: 1 });
    await pick([note('big.txt', 2)]);
    await waitFor(() => expect(screen.getByTestId('conversation-error').textContent).toContain('File too large.'));
    expect(screen.queryByTestId('attach-chips')).toBeNull();
  });

  it('the paperclip is disabled once the conversation is closed', () => {
    view({ closed: true });
    expect((screen.getByTestId('attach-button') as HTMLButtonElement).disabled).toBe(true);
  });
});
