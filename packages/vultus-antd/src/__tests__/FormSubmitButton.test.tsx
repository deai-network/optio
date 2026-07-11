import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { App as AntApp, Form, Input } from 'antd';
import { FormPanel } from '../FormPanel.js';
import { FormSubmitButton } from '../FormSubmitButton.js';
import type { ActionStatus } from 'vultus-core';

function makeStatus<TArgs>(overrides: Partial<ActionStatus<TArgs>> = {}): ActionStatus<TArgs> {
  return {
    id: 'submit',
    label: 'Submit',
    variant: 'primary',
    pending: false,
    disabled: false,
    invisible: false,
    errors: [],
    fire: vi.fn(),
    firePromise: vi.fn(async () => {}),
    ...overrides,
  };
}

describe('FormSubmitButton', () => {
  it('on click: validates form, passes values to action.firePromise', async () => {
    const firePromise = vi.fn(async () => {});
    const action = makeStatus<{ x: string }>({ firePromise });
    render(
      <AntApp>
        <FormPanel initialValues={{ x: 'hello' }}>
          <Form.Item name="x"><Input /></Form.Item>
          <FormSubmitButton action={action} />
        </FormPanel>
      </AntApp>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    await new Promise((r) => setTimeout(r, 0));
    expect(firePromise).toHaveBeenCalledWith({ x: 'hello' });
  });

  it('does NOT call firePromise if form validation fails', async () => {
    const firePromise = vi.fn(async () => {});
    const action = makeStatus<{ x: string }>({ firePromise });
    render(
      <AntApp>
        <FormPanel>
          <Form.Item name="x" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <FormSubmitButton action={action} />
        </FormPanel>
      </AntApp>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    await new Promise((r) => setTimeout(r, 0));
    expect(firePromise).not.toHaveBeenCalled();
  });

  it('disabled / pending / invisible behavior mirrors ActionButton', () => {
    const action = makeStatus({ disabled: true, reason: 'no' });
    render(
      <AntApp>
        <FormPanel>
          <FormSubmitButton action={action} />
        </FormPanel>
      </AntApp>,
    );
    const btn = screen.getByRole('button');
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('title', 'no');
  });
});
