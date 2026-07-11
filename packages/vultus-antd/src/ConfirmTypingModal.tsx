import { useEffect, useState } from 'react';
import { Modal, Input } from 'antd';
import type React from 'react';

interface Props {
  open: boolean;
  title: string;
  entityName: string;
  description: React.ReactNode;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmTypingModal({
  open, title, entityName, description, onConfirm, onCancel,
}: Props) {
  const [typed, setTyped] = useState('');
  useEffect(() => {
    if (open) setTyped('');
  }, [open]);

  const matches = typed === entityName;

  return (
    <Modal
      open={open}
      title={title}
      onCancel={onCancel}
      onOk={onConfirm}
      okText="Delete"
      okButtonProps={{ danger: true, disabled: !matches }}
    >
      {description}
      <p style={{ marginTop: 12 }}>
        Type <code>{entityName}</code> to confirm:
      </p>
      <Input value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus />
    </Modal>
  );
}
