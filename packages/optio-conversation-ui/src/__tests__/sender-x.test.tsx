import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';

// Ant Design X experiment (owner-approved 2026-10-12): the composer is an X
// Sender. Our textarea fills its input slot (test id and flash animation
// kept), today's toolbar (session controls; paperclip, send, interrupt) is its
// footer, attached files show in its header, and X's own send button is off:
// our buttons and keys carry the send/steer/interrupt logic.

function view(over: Partial<ConversationViewProps> = {}) {
  const onSend = vi.fn(async () => true);
  const props: ConversationViewProps = {
    state: { items: [], busy: false, closed: false, controls: [] },
    closed: false, busy: false, toolVerbosity: 'verbose', thinkingVerbosity: 'visible',
    showFileUpload: true, maxUploadBytes: 1000, fileDownload: false,
    onSend, onInterrupt: vi.fn(), onPermission: vi.fn(), onFileDownload: vi.fn(),
    ...over,
  };
  render(<ConversationView {...props} />);
  return { onSend: props.onSend as typeof onSend, onInterrupt: props.onInterrupt as ReturnType<typeof vi.fn> };
}

const sender = () => document.querySelector('.ant-sender') as HTMLElement | null;
const box = () => screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;

describe('X Sender', () => {
  it('our textarea fills its input slot and the toolbar is its footer; no X send button', () => {
    view();
    expect(sender()).not.toBeNull();
    expect(sender()!.contains(box())).toBe(true);
    expect(box().tagName).toBe('TEXTAREA');
    expect(box().classList.contains('optio-cc-flash')).toBe(true);
    expect(sender()!.querySelector('.ant-sender-footer')?.contains(screen.getByTestId('session-toolbar'))).toBe(true);
    expect(sender()!.querySelector('.ant-sender-actions-list')).toBeNull();
    // One element carries the input's test id (not Sender's root as well).
    expect(document.querySelectorAll('[data-testid="conversation-input-box"]')).toHaveLength(1);
  });

  it('attached files show in its header', async () => {
    view();
    const picker = screen.getByTestId('attach-button').closest('.ant-upload')!.querySelector('input[type="file"]')!;
    await act(async () => {
      fireEvent.change(picker, { target: { files: [new File([new Uint8Array(2)], 'note.txt', { type: 'text/plain' })] } });
    });
    await waitFor(() => expect(sender()!.querySelector('.ant-sender-header [data-testid="attach-chips"]')).not.toBeNull());
  });

  it('Enter sends through our handler once; Shift+Enter does not send', async () => {
    const { onSend } = view();
    fireEvent.change(box(), { target: { value: 'hello' } });
    fireEvent.keyDown(box(), { key: 'Enter', shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.keyDown(box(), { key: 'Enter' });
    });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect((onSend.mock.calls[0] as unknown as [string])[0]).toBe('hello');
  });

  it('Esc interrupts a running turn', async () => {
    // onInterrupt returns its promise, as the engine views do: the window-level
    // Esc handler then finds the interrupt in flight and adds no second call.
    const { onInterrupt } = view({
      busy: true, state: { items: [], busy: true, closed: false, controls: [] }, onInterrupt: vi.fn(async () => true),
    });
    await act(async () => {
      fireEvent.keyDown(box(), { key: 'Escape' });
    });
    expect(onInterrupt).toHaveBeenCalledTimes(1);
  });

  it('is disabled once the conversation is closed', () => {
    view({ closed: true });
    expect(box().disabled).toBe(true);
  });
});
