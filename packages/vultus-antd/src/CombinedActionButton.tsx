import { useState } from 'react';
import { Button, Dropdown, Modal, Popconfirm, Tooltip, theme } from 'antd';
import type { ActionStatus } from 'vultus-core';
import { ConfirmTypingModal } from './ConfirmTypingModal.js';
import { ActionButton } from './ActionButton.js';
import { ReasonMarkdown } from './ReasonMarkdown.js';

interface Props {
  actions: ActionStatus[];
  size?: 'small' | 'middle' | 'large';
}

export function CombinedActionButton({ actions, size }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectionSource, setSelectionSource] = useState<'auto' | 'manual'>('auto');
  const [typingOpen, setTypingOpen] = useState(false);
  const [popconfirmOpen, setPopconfirmOpen] = useState(false);
  const { token } = theme.useToken();

  const visible = actions.filter((a) => !a.invisible);
  if (visible.length === 0) return null;
  if (visible.length === 1) return <ActionButton action={visible[0]} size={size} />;

  // Manual picks stay put even when the picked action becomes disabled
  // (operator's choice is respected — button disables but selection holds).
  // Auto picks re-evaluate to first-enabled each render, so the default
  // tracks state changes without any effect.
  const selectedIndex = (() => {
    if (selectionSource === 'manual' && selectedId !== null) {
      const idx = visible.findIndex((a) => a.id === selectedId);
      if (idx !== -1) return idx;
    }
    const firstEnabled = visible.findIndex((a) => !a.disabled);
    return firstEnabled !== -1 ? firstEnabled : 0;
  })();

  const active = visible[selectedIndex];

  // Fire an explicit action (not the resolved `active`). Menu rows cannot
  // "select then read active" in one handler — `active` only recomputes on
  // the next render — so the action is passed in explicitly. Selecting the
  // action also locks it as a manual pick (intent = the click, regardless of
  // whether the operator follows through with confirmation), mirroring the
  // old lockActive behavior. Used by both the main button and the menu rows.
  const fire = (action: ActionStatus) => {
    if (action.disabled || action.pending) return;
    setSelectedId(action.id);
    setSelectionSource('manual');
    if (!action.confirmation) {
      action.fire();
      return;
    }
    if (action.confirmation.kind === 'typing') {
      setTypingOpen(true);
      return;
    }
    if (action.confirmation.kind === 'cascade-modal') {
      const conf = action.confirmation;
      Modal.confirm({
        title: conf.title,
        content: conf.content,
        okText: action.label,
        okButtonProps: { danger: action.variant === 'danger' },
        onOk: () => action.fire(),
      });
      return;
    }
    // popconfirm: open the controlled bubble on the main half.
    setPopconfirmOpen(true);
  };

  const menuItems = visible.map((a, i) => {
    // primary → label tinted with the same token antd's primary button fills
    // with (colorPrimary), bold; deliberately no solid fill so it does not
    // collide with the selectedKeys highlight on the active row. danger uses
    // antd's native menu-item danger styling (set below via `danger`).
    const labelText =
      a.variant === 'primary'
        ? <span style={{ color: token.colorPrimary, fontWeight: 600 }}>{a.label}</span>
        : <span>{a.label}</span>;
    return {
      key: String(i),
      icon: a.icon,
      danger: a.variant === 'danger',
      label: a.reason
        ? <Tooltip title={<ReasonMarkdown>{a.reason}</ReasonMarkdown>}>{labelText}</Tooltip>
        : labelText,
      disabled: a.disabled,
      onClick: () => fire(a),
    };
  });

  // Compose the main half explicitly via buttonsRender so the chevron stays
  // interactive even when the active half is disabled (design §3.2 requires
  // the menu to open in the all-disabled case for per-item reason tooltips),
  // the icon renders via Button's `icon` prop, and Popconfirm wraps only the
  // main half (chevron click cannot trigger it).
  const renderMainButton = () => {
    const rawButton = (
      <Button
        icon={active.icon}
        type={active.variant === 'primary' ? 'primary' : 'default'}
        danger={active.variant === 'danger'}
        size={size}
        loading={active.pending}
        disabled={active.disabled || active.pending}
        // Single path for every confirmation kind. For popconfirm, fire()
        // opens the controlled bubble (the Popconfirm no longer auto-opens
        // on child click now that it is controlled).
        onClick={() => fire(active)}
        data-action-id={active.id}
      >
        {active.label}
      </Button>
    );

    // Mirror single-action: disabled buttons have pointer-events:none which
    // swallows tooltip hover; wrap in Tooltip><span> to restore it.
    const withTooltip = active.reason
      ? (
          <Tooltip title={<ReasonMarkdown>{active.reason}</ReasonMarkdown>}>
            <span style={{ display: 'inline-block', cursor: active.disabled ? 'not-allowed' : undefined }}>
              {rawButton}
            </span>
          </Tooltip>
        )
      : rawButton;

    if (active.confirmation?.kind === 'popconfirm') {
      return (
        <Popconfirm
          title={<div style={{ maxWidth: 280, whiteSpace: 'normal' }}>{active.confirmation.question}</div>}
          // Controlled: fire() drives `open` (from the main button OR a menu
          // row). We only handle the close transition here — opening is always
          // explicit via fire(). onConfirm closes via the same false transition.
          open={popconfirmOpen}
          onOpenChange={(next) => { if (!next) setPopconfirmOpen(false); }}
          onConfirm={() => active.fire()}
          okButtonProps={{ danger: active.variant === 'danger' }}
          disabled={active.disabled}
        >
          {withTooltip}
        </Popconfirm>
      );
    }

    return withTooltip;
  };

  const dropdownButton = (
    <Dropdown.Button
      // Click trigger (default is hover) — touch devices can't hover, and
      // chevron is meant to be clicked anyway.
      trigger={['click']}
      // Size the whole compound (esp. the chevron half) — without this the
      // dropdown trigger renders at the default size even when the main button
      // is `small`. The main half re-applies size via renderMainButton.
      size={size}
      menu={{ items: menuItems, selectedKeys: [String(selectedIndex)] }}
      type={active.variant === 'primary' ? 'primary' : 'default'}
      danger={active.variant === 'danger'}
      buttonsRender={([_left, right]) => [renderMainButton(), right]}
      // Override antd's hardcoded `block: true` on the inner Space.Compact
      // wrapper. Block-mode adds `display:flex; width:100%` which makes the
      // button greedy under a flex parent (eg. the entity-detail heading's
      // `space-between` layout) and crushes the title to its left.
      // restProps in dropdown-button.js are spread AFTER its hardcoded block
      // pair so this `block={false}` actually wins.
      // @ts-expect-error — antd's DropdownButtonProps does not declare block,
      // but Space.Compact does and restProps is forwarded to it verbatim.
      block={false}
    />
  );

  if (active.confirmation?.kind === 'typing') {
    const conf = active.confirmation;
    return (
      <>
        {dropdownButton}
        <ConfirmTypingModal
          open={typingOpen}
          title={conf.title}
          entityName={conf.entityName}
          description={conf.description}
          onConfirm={() => { setTypingOpen(false); active.fire(); }}
          onCancel={() => setTypingOpen(false)}
        />
      </>
    );
  }

  return dropdownButton;
}
