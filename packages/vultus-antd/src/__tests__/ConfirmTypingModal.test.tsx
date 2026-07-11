import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { App as AntApp } from 'antd';
import { ConfirmTypingModal } from '../ConfirmTypingModal.js';

describe('ConfirmTypingModal', () => {
  it('OK disabled until typed string matches entityName', () => {
    render(
      <AntApp>
        <ConfirmTypingModal
          open
          title="Delete?"
          entityName="myproj"
          description="permanent"
          onConfirm={vi.fn()}
          onCancel={vi.fn()}
        />
      </AntApp>,
    );
    const okBtn = screen.getByRole('button', { name: 'Delete' });
    expect(okBtn).toBeDisabled();
    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: 'myproj' } });
    expect(okBtn).not.toBeDisabled();
  });

  it('Cancel triggers onCancel', () => {
    const onCancel = vi.fn();
    render(
      <AntApp>
        <ConfirmTypingModal
          open
          title="X"
          entityName="x"
          description="x"
          onConfirm={vi.fn()}
          onCancel={onCancel}
        />
      </AntApp>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalled();
  });

  it('Confirm triggers onConfirm', () => {
    const onConfirm = vi.fn();
    render(
      <AntApp>
        <ConfirmTypingModal
          open
          title="X"
          entityName="x"
          description="x"
          onConfirm={onConfirm}
          onCancel={vi.fn()}
        />
      </AntApp>,
    );
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(onConfirm).toHaveBeenCalled();
  });
});
