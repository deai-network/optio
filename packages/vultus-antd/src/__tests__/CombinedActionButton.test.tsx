import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { App as AntApp, Modal } from 'antd';
import { ActionButton } from '../ActionButton.js';
import { makeStatus } from './helpers/makeStatus.js';

function wrap(node: React.ReactNode) {
  return <AntApp>{node}</AntApp>;
}

// Query helpers — prefer documented AntD classes over DOM-position indexes.
function mainBtn(container: HTMLElement): HTMLElement {
  // Dropdown.Button renders: <Button.Group><MainButton/><DropdownTrigger/></>.
  // The trigger has class `ant-dropdown-trigger`; everything else is the main.
  const btn = container.querySelector('.ant-btn:not(.ant-dropdown-trigger)');
  if (!btn) throw new Error('main button not found');
  return btn as HTMLElement;
}

function chevronBtn(container: HTMLElement): HTMLElement {
  const btn = container.querySelector('.ant-dropdown-trigger');
  if (!btn) throw new Error('chevron not found');
  return btn as HTMLElement;
}

describe('ActionButton — combined mode', () => {
  // Modal.confirm mounts imperatively on document.body and is NOT torn down by
  // RTL's auto-cleanup (which only unmounts container-rendered trees). Destroy
  // any leaked confirm modals between tests so identical titles don't collide.
  afterEach(() => { Modal.destroyAll(); });

  it('renders the active selection label in the main button', () => {
    const a = makeStatus({ id: 'a', label: 'Sync now' });
    const b = makeStatus({ id: 'b', label: 'Sync now (dry-run)' });
    const { container } = render(wrap(<ActionButton action={[a, b]} />));
    expect(mainBtn(container).textContent).toContain('Sync now');
    expect(mainBtn(container).textContent).not.toContain('dry-run');
  });

  it('main click fires the active (first-enabled) action', () => {
    const a = makeStatus({ id: 'a', label: 'A' });
    const b = makeStatus({ id: 'b', label: 'B' });
    const { container } = render(wrap(<ActionButton action={[a, b]} />));
    fireEvent.click(mainBtn(container));
    expect(a.fire).toHaveBeenCalledTimes(1);
    expect(b.fire).not.toHaveBeenCalled();
  });

  it('menu-item click fires that action and moves selection to it', async () => {
    const a = makeStatus({ id: 'a', label: 'A' });
    const b = makeStatus({ id: 'b', label: 'B' });
    const { container } = render(wrap(<ActionButton action={[a, b]} />));
    fireEvent.click(chevronBtn(container));
    const bItem = await screen.findByRole('menuitem', { name: 'B' });
    fireEvent.click(bItem);
    // Clicking B in the menu fires B (and only B).
    expect(b.fire).toHaveBeenCalledTimes(1);
    expect(a.fire).not.toHaveBeenCalled();
    // Selection moves to B — the main button now shows B.
    await waitFor(() => {
      expect(mainBtn(container).textContent).toContain('B');
    });
  });

  it('clicking a disabled menu option does not fire it', async () => {
    const a = makeStatus({ id: 'a', label: 'A' });
    const b = makeStatus({ id: 'b', label: 'B', disabled: true, reason: 'nope' });
    const { container } = render(wrap(<ActionButton action={[a, b]} />));
    fireEvent.click(chevronBtn(container));
    const bItem = await screen.findByRole('menuitem', { name: 'B' });
    fireEvent.click(bItem);
    expect(b.fire).not.toHaveBeenCalled();
  });

  it('falls back to a plain button when only one action is visible', () => {
    const a = makeStatus({ id: 'a', label: 'Visible' });
    const b = makeStatus({ id: 'b', label: 'Hidden', invisible: true });
    const { container } = render(wrap(<ActionButton action={[a, b]} />));
    // No chevron — single visible action renders as a plain ActionButton.
    expect(container.querySelector('.ant-dropdown-trigger')).toBeNull();
    expect(screen.getByRole('button', { name: 'Visible' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Hidden' })).toBeNull();
  });

  it('renders nothing when all actions are invisible', () => {
    const a = makeStatus({ id: 'a', invisible: true });
    const b = makeStatus({ id: 'b', invisible: true });
    const { container } = render(wrap(<ActionButton action={[a, b]} />));
    expect(container.firstChild?.firstChild).toBeNull();
  });

  it('all-disabled: main button disabled, menu still opens, items show aria-disabled', async () => {
    const a = makeStatus({ id: 'a', label: 'A', disabled: true, reason: 'because A' });
    const b = makeStatus({ id: 'b', label: 'B', disabled: true, reason: 'because B' });
    const { container } = render(wrap(<ActionButton action={[a, b]} />));

    expect(mainBtn(container)).toBeDisabled();
    expect(chevronBtn(container)).not.toBeDisabled();

    fireEvent.click(chevronBtn(container));
    const aItem = await screen.findByRole('menuitem', { name: 'A' });
    const bItem = await screen.findByRole('menuitem', { name: 'B' });
    expect(aItem).toHaveAttribute('aria-disabled', 'true');
    expect(bItem).toHaveAttribute('aria-disabled', 'true');
  });

  it('auto-shifts default selection when previously-default action becomes disabled', () => {
    const a = makeStatus({ id: 'a', label: 'A' });
    const b = makeStatus({ id: 'b', label: 'B' });
    const { rerender, container } = render(wrap(<ActionButton action={[a, b]} />));
    expect(mainBtn(container).textContent).toContain('A');

    const aDisabled = { ...a, disabled: true };
    rerender(wrap(<ActionButton action={[aDisabled, b]} />));
    expect(mainBtn(container).textContent).toContain('B');
  });

  it('manual selection sticks even when it becomes disabled', async () => {
    const a = makeStatus({ id: 'a', label: 'A' });
    const b = makeStatus({ id: 'b', label: 'B' });
    const { container, rerender } = render(wrap(<ActionButton action={[a, b]} />));

    fireEvent.click(chevronBtn(container));
    const bItem = await screen.findByRole('menuitem', { name: 'B' });
    fireEvent.click(bItem);
    await waitFor(() => {
      expect(mainBtn(container).textContent).toContain('B');
    });

    const bDisabled = { ...b, disabled: true };
    rerender(wrap(<ActionButton action={[a, bDisabled]} />));
    // Manual pick on B sticks. Main button disables.
    expect(mainBtn(container).textContent).toContain('B');
    expect(mainBtn(container)).toBeDisabled();
    // Chevron stays interactive — operator can switch back if they want.
    expect(chevronBtn(container)).not.toBeDisabled();
  });

  it('honors active selection popconfirm on main click; chevron click does not trigger it', async () => {
    const a = makeStatus({
      id: 'a',
      label: 'Delete',
      variant: 'danger',
      confirmation: { kind: 'popconfirm', question: 'Sure?' },
    });
    const b = makeStatus({ id: 'b', label: 'Other' });
    const { container } = render(wrap(<ActionButton action={[a, b]} />));

    // Chevron click opens the menu; the popconfirm question must NOT appear.
    fireEvent.click(chevronBtn(container));
    await screen.findByRole('menuitem', { name: 'Delete' });
    expect(screen.queryByText('Sure?')).toBeNull();

    // Click main button — popconfirm question appears.
    fireEvent.click(mainBtn(container));
    expect(await screen.findByText('Sure?')).toBeInTheDocument();
    expect(a.fire).not.toHaveBeenCalled(); // not until OK clicked
  });

  it('opens cascade-modal on main click for active with cascade-modal confirmation', async () => {
    const a = makeStatus({
      id: 'a',
      label: 'Reset',
      variant: 'danger',
      confirmation: { kind: 'cascade-modal', title: 'Reset entity?', content: 'Are you sure?' },
    });
    const b = makeStatus({ id: 'b', label: 'Other' });
    const { container } = render(wrap(<ActionButton action={[a, b]} />));

    fireEvent.click(mainBtn(container));
    // Modal renders title in `.ant-modal-title` plus a sr-only aria label;
    // scope the query to the title element.
    await waitFor(() => {
      expect(document.body.querySelector('.ant-modal-title')?.textContent).toBe('Reset entity?');
    });
    expect(a.fire).not.toHaveBeenCalled();
  });

  it('opens typing-confirm modal on main click for active with typing confirmation', async () => {
    const a = makeStatus({
      id: 'a',
      label: 'Delete',
      variant: 'danger',
      confirmation: { kind: 'typing', title: 'Delete entity', entityName: 'orders', description: 'Type the entity name to confirm.' },
    });
    const b = makeStatus({ id: 'b', label: 'Other' });
    const { container } = render(wrap(<ActionButton action={[a, b]} />));

    fireEvent.click(mainBtn(container));
    expect(await screen.findByText('Delete entity')).toBeInTheDocument();
    expect(a.fire).not.toHaveBeenCalled();
  });

  it('variant + danger follow the active selection (not actions[0])', async () => {
    const a = makeStatus({ id: 'a', label: 'A', variant: 'primary' });
    const b = makeStatus({ id: 'b', label: 'B', variant: 'danger' });
    const { container } = render(wrap(<ActionButton action={[a, b]} />));

    expect(mainBtn(container).className).toContain('ant-btn-primary');
    expect(mainBtn(container).className).not.toContain('ant-btn-dangerous');

    fireEvent.click(chevronBtn(container));
    const bItem = await screen.findByRole('menuitem', { name: 'B' });
    fireEvent.click(bItem);

    await waitFor(() => {
      expect(mainBtn(container).className).toContain('ant-btn-dangerous');
    });
    expect(mainBtn(container).className).not.toContain('ant-btn-primary');
  });

  it('menu-fire of a popconfirm action opens the bubble; fires only on confirm', async () => {
    const a = makeStatus({ id: 'a', label: 'A' });
    const b = makeStatus({
      id: 'b',
      label: 'Delete',
      variant: 'danger',
      confirmation: { kind: 'popconfirm', question: 'Sure?' },
    });
    const { container } = render(wrap(<ActionButton action={[a, b]} />));

    fireEvent.click(chevronBtn(container));
    const bItem = await screen.findByRole('menuitem', { name: 'Delete' });
    fireEvent.click(bItem);

    // Bubble appears (on the now-active main button); fire not yet called.
    expect(await screen.findByText('Sure?')).toBeInTheDocument();
    expect(b.fire).not.toHaveBeenCalled();

    // Confirm → fires.
    fireEvent.click(screen.getByRole('button', { name: /^OK$/i }));
    await waitFor(() => expect(b.fire).toHaveBeenCalledTimes(1));
  });

  it('menu-fire of a cascade-modal action opens the modal; fires on OK', async () => {
    const a = makeStatus({ id: 'a', label: 'A' });
    const b = makeStatus({
      id: 'b',
      label: 'Reset',
      variant: 'danger',
      confirmation: { kind: 'cascade-modal', title: 'Reset entity?', content: 'Are you sure?' },
    });
    const { container } = render(wrap(<ActionButton action={[a, b]} />));

    fireEvent.click(chevronBtn(container));
    const bItem = await screen.findByRole('menuitem', { name: 'Reset' });
    fireEvent.click(bItem);

    // Title renders in `.ant-modal-title` plus a sr-only confirm-title; scope
    // to the title element rather than matching the duplicated text.
    await waitFor(() => {
      expect(document.body.querySelector('.ant-modal-title')?.textContent).toBe('Reset entity?');
    });
    expect(b.fire).not.toHaveBeenCalled();
  });

  it('menu-fire of a typing action opens the typing modal', async () => {
    const a = makeStatus({ id: 'a', label: 'A' });
    const b = makeStatus({
      id: 'b',
      label: 'Delete',
      variant: 'danger',
      confirmation: { kind: 'typing', title: 'Delete entity', entityName: 'orders', description: 'Type the name to confirm.' },
    });
    const { container } = render(wrap(<ActionButton action={[a, b]} />));

    fireEvent.click(chevronBtn(container));
    const bItem = await screen.findByRole('menuitem', { name: 'Delete' });
    fireEvent.click(bItem);

    expect(await screen.findByText('Delete entity')).toBeInTheDocument();
    expect(b.fire).not.toHaveBeenCalled();
  });

  it('menu options reflect variant (danger/primary) and render icons', async () => {
    const a = makeStatus({
      id: 'a', label: 'Go', variant: 'primary',
      icon: <span data-testid="icon-a" />,
    });
    const b = makeStatus({
      id: 'b', label: 'Delete', variant: 'danger',
      icon: <span data-testid="icon-b" />,
    });
    const { container } = render(wrap(<ActionButton action={[a, b]} />));
    fireEvent.click(chevronBtn(container));

    const goItem = await screen.findByRole('menuitem', { name: 'Go' });
    const delItem = await screen.findByRole('menuitem', { name: 'Delete' });

    // danger → antd native danger class on the row (dropdown menu prefix).
    expect(delItem.className).toContain('ant-dropdown-menu-item-danger');

    // primary → label carries an explicit color style (colorPrimary token,
    // resolved to an inline color on the label span). Assert the styled span
    // exists rather than the exact hex (theme-dependent).
    const primarySpan = goItem.querySelector('span[style*="color"]');
    expect(primarySpan).not.toBeNull();
    expect(primarySpan?.textContent).toContain('Go');

    // icons render per row. (icon-a also appears on the main button — it is
    // the active selection — so scope the assertion to the menu rows.)
    expect(goItem.querySelector('[data-testid="icon-a"]')).not.toBeNull();
    expect(delItem.querySelector('[data-testid="icon-b"]')).not.toBeNull();
  });
});
