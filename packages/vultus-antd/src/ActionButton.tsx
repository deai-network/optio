import { useState } from 'react';
import { Button, Modal, Popconfirm, Tooltip } from 'antd';
import type { ActionStatus } from 'vultus-core';
import { ConfirmTypingModal } from './ConfirmTypingModal.js';
import { CombinedActionButton } from './CombinedActionButton.js';
import { ReasonMarkdown } from './ReasonMarkdown.js';

interface Props {
  action: ActionStatus | ActionStatus[];
  size?: 'small' | 'middle' | 'large';
  block?: boolean;
}

export function ActionButton({ action, size, block }: Props) {
  // `useState` must be called unconditionally on every render to keep the
  // hook call order stable. The `action` prop can plausibly flip shape
  // across renders, so we cannot guard this hook behind the Array.isArray
  // branch — that would change the hook count and trip Rules of Hooks.
  const [typingOpen, setTypingOpen] = useState(false);

  if (Array.isArray(action)) {
    return <CombinedActionButton actions={action} size={size} />;
  }

  if (action.invisible) return null;

  const handleClick = () => {
    if (action.disabled || action.pending) return;
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
    // popconfirm wraps the button; click is delegated to Popconfirm.
  };

  const rawButton = (
    <Button
      icon={action.icon}
      type={action.variant === 'primary' ? 'primary' : 'default'}
      danger={action.variant === 'danger'}
      size={size}
      block={block}
      loading={action.pending}
      disabled={action.disabled || action.pending}
      onClick={action.confirmation?.kind === 'popconfirm' ? undefined : handleClick}
      data-action-id={action.id}
    >
      {action.label}
    </Button>
  );

  // AntD's disabled buttons set `pointer-events: none`, which suppresses the
  // native `title` attribute's hover tooltip. Wrap in <Tooltip><span>...</span></Tooltip>
  // so disabled-with-reason actually shows the reason on hover. Only wrap
  // when there's something to say.
  const buttonNode = action.reason
    ? (
        <Tooltip title={<ReasonMarkdown>{action.reason}</ReasonMarkdown>}>
          <span style={{ display: 'inline-block', cursor: action.disabled ? 'not-allowed' : undefined }}>
            {rawButton}
          </span>
        </Tooltip>
      )
    : rawButton;

  if (action.confirmation?.kind === 'popconfirm') {
    return (
      <Popconfirm
        title={<div style={{ maxWidth: 280, whiteSpace: 'normal' }}>{action.confirmation.question}</div>}
        onConfirm={() => action.fire()}
        okButtonProps={{ danger: action.variant === 'danger' }}
        disabled={action.disabled}
      >
        {buttonNode}
      </Popconfirm>
    );
  }

  if (action.confirmation?.kind === 'typing') {
    const conf = action.confirmation;
    return (
      <>
        {buttonNode}
        <ConfirmTypingModal
          open={typingOpen}
          title={conf.title}
          entityName={conf.entityName}
          description={conf.description}
          onConfirm={() => {
            setTypingOpen(false);
            action.fire();
          }}
          onCancel={() => setTypingOpen(false)}
        />
      </>
    );
  }

  return buttonNode;
}
