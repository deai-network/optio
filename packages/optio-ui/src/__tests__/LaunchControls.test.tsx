import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18next from 'i18next';

import { LaunchControls } from '../components/LaunchControls.js';

const i18n = i18next.createInstance();
i18n.init({ lng: 'en', resources: { en: { translation: {} } } });

function renderWith(process: any, onLaunch = vi.fn()) {
  return {
    onLaunch,
    ...render(
      <I18nextProvider i18n={i18n}>
        <LaunchControls process={process} onLaunch={onLaunch} size="small" />
      </I18nextProvider>,
    ),
  };
}

describe('LaunchControls', () => {
  it('renders nothing when process is in a non-launchable state', () => {
    const { container } = renderWith({ _id: '1', status: { state: 'running' } });
    expect(container.firstChild).toBeNull();
  });

  it('renders a single play button when supportsResume=false', () => {
    const { onLaunch } = renderWith({
      _id: '1', status: { state: 'idle' }, supportsResume: false, hasSavedState: false,
    });
    const btn = screen.getByRole('button');
    fireEvent.click(btn);
    expect(onLaunch).toHaveBeenCalledWith('1', undefined);
  });

  it('renders a single play button when supportsResume=true but hasSavedState=false', () => {
    const { onLaunch } = renderWith({
      _id: '2', status: { state: 'idle' }, supportsResume: true, hasSavedState: false,
    });
    const btns = screen.getAllByRole('button');
    expect(btns.length).toBe(1);
    fireEvent.click(btns[0]);
    expect(onLaunch).toHaveBeenCalledWith('2', undefined);
  });

  it('renders a split button when supportsResume=true AND hasSavedState=true', () => {
    const { onLaunch } = renderWith({
      _id: '3', status: { state: 'idle' }, supportsResume: true, hasSavedState: true,
    });
    const primary = screen.getAllByRole('button')[0];
    fireEvent.click(primary);
    expect(onLaunch).toHaveBeenCalledWith('3', { resume: true });
  });

  it('dropdown item dispatches resume=false', async () => {
    const { onLaunch } = renderWith({
      _id: '4', status: { state: 'idle' }, supportsResume: true, hasSavedState: true,
    });
    const buttons = screen.getAllByRole('button');
    const dropdownTrigger = buttons[buttons.length - 1];
    fireEvent.click(dropdownTrigger);
    const restart = await screen.findByText(/restart/i);
    fireEvent.click(restart);
    expect(onLaunch).toHaveBeenCalledWith('4', { resume: false });
  });

  it('treats missing supportsResume / hasSavedState as false', () => {
    const { onLaunch } = renderWith({ _id: '5', status: { state: 'idle' } });
    const btn = screen.getByRole('button');
    fireEvent.click(btn);
    expect(onLaunch).toHaveBeenCalledWith('5', undefined);
  });
});
