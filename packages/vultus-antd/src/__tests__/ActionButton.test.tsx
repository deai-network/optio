import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { App as AntApp } from 'antd';
import { ActionButton } from '../ActionButton.js';
import { makeStatus } from './helpers/makeStatus.js';

function wrap(node: React.ReactNode) {
  return <AntApp>{node}</AntApp>;
}

describe('ActionButton', () => {
  it('renders nothing when invisible', () => {
    const { container } = render(wrap(<ActionButton action={makeStatus({ invisible: true })} />));
    expect(container.firstChild?.firstChild).toBeNull();
  });

  it('pending → disabled', () => {
    render(wrap(<ActionButton action={makeStatus({ pending: true })} />));
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('disabled → disabled; reason exposed via AntD Tooltip on hover', async () => {
    render(wrap(<ActionButton action={makeStatus({ disabled: true, reason: 'because' })} />));
    const btn = screen.getByRole('button');
    expect(btn).toBeDisabled();
    // AntD wraps the disabled button in a span (workaround for AntD's
    // pointer-events:none on disabled buttons swallowing native hover).
    // Reason becomes Tooltip.title; on hover, Tooltip renders into a portal.
    // The span sits between the button and the Tooltip mount; hovering it
    // triggers the Tooltip's mouseenter handler.
    fireEvent.mouseEnter(btn.parentElement!);
    await waitFor(() => {
      expect(screen.getByText('because')).toBeInTheDocument();
    });
  });

  it('no confirmation → onClick fires action', () => {
    const fire = vi.fn();
    render(wrap(<ActionButton action={makeStatus({ fire })} />));
    fireEvent.click(screen.getByRole('button'));
    expect(fire).toHaveBeenCalled();
  });

  it('popconfirm: wraps button; onConfirm fires action', async () => {
    const fire = vi.fn();
    render(
      wrap(
        <ActionButton
          action={makeStatus({
            fire,
            confirmation: { kind: 'popconfirm', question: 'Sure?' },
          })}
        />,
      ),
    );
    fireEvent.click(screen.getByRole('button'));
    expect(await screen.findByText('Sure?')).toBeInTheDocument();
    const okBtn = screen.getAllByRole('button').find((b) => b.textContent?.includes('OK'));
    if (okBtn) fireEvent.click(okBtn);
    expect(fire).toHaveBeenCalled();
  });
});
