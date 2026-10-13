import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { theme } from 'antd';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';

// An answer's footer (owner, 2026-10-14): the copy action, beside the time,
// shows only while the answer is hovered (or holds focus); and every bubble's
// time sits close under its own bubble, not nearer the next one.

function viewProps(over: Partial<ConversationViewProps> = {}): ConversationViewProps {
  return {
    state: {
      items: [
        { kind: 'user', text: 'hi there', seq: 1, timestamp: Date.parse('2026-10-14T09:00:00Z') },
        { kind: 'assistant', text: 'Hello.', pending: false, seq: 2, msgId: 'm1', timestamp: Date.parse('2026-10-14T09:00:05Z') },
      ],
      busy: false, closed: false, controls: [],
    },
    closed: false, busy: false, toolVerbosity: 'verbose', thinkingVerbosity: 'visible',
    showFileUpload: false, maxUploadBytes: 1, fileDownload: false,
    onSend: vi.fn(async () => true), onInterrupt: vi.fn(), onPermission: vi.fn(), onFileDownload: vi.fn(),
    ...over,
  };
}

describe("an answer's footer", () => {
  it('keeps the copy action beside the time, revealed by hovering or focusing the answer', () => {
    render(<ConversationView {...viewProps()} />);
    const copy = screen.getByTestId('answer-copy');
    expect(copy.closest('.ant-bubble-footer')).not.toBeNull();
    expect(copy.classList.contains('optio-cc-copy')).toBe(true);
    expect(copy.closest('.optio-cc-answer-bubble')).not.toBeNull();
    const css = document.getElementById('optio-cc-copy-style')!.textContent!.replace(/\s+/g, ' ');
    // Hidden by opacity (the keyboard still reaches it), shown on hover or
    // focus, and always on devices without hover.
    expect(css).toContain('.optio-cc-answer-bubble .optio-cc-copy{opacity:0}');
    expect(css).toContain('.optio-cc-answer-bubble:hover .optio-cc-copy,.optio-cc-answer-bubble:focus-within .optio-cc-copy{opacity:1}');
    expect(css).toContain('@media (hover:none){.optio-cc-answer-bubble .optio-cc-copy{opacity:1}}');
  });

  it('puts every bubble\'s time close under it and apart from the next row (X leaves a full margin above a footer)', () => {
    render(<ConversationView {...viewProps()} />);
    const footers = [...document.querySelectorAll<HTMLElement>('[data-testid="conversation-content"] .ant-bubble-footer')];
    expect(footers).toHaveLength(2);
    const { marginXXS, marginXS } = theme.getDesignToken();
    for (const footer of footers) {
      expect(footer.style.marginTop).toBe(`${marginXXS}px`);
      expect(footer.style.marginBottom).toBe(`${marginXS}px`);
    }
  });
});
