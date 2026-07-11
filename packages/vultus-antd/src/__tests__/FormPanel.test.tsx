import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { App as AntApp, Form, Input } from 'antd';
import { FormPanel } from '../FormPanel.js';

describe('FormPanel', () => {
  it('renders children with provider scope', () => {
    render(
      <AntApp>
        <FormPanel>
          <Form.Item name="x" label="X"><Input /></Form.Item>
        </FormPanel>
      </AntApp>,
    );
    expect(screen.getByLabelText('X')).toBeInTheDocument();
  });

  it('does not render submit button itself (consumer renders FormSubmitButton)', () => {
    render(
      <AntApp>
        <FormPanel>
          <Form.Item name="x"><Input /></Form.Item>
        </FormPanel>
      </AntApp>,
    );
    expect(screen.queryByRole('button')).toBeNull();
  });
});
