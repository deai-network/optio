import type React from 'react';
import { Spin } from 'antd';
import { useSpinnerKey, useVultusComponents } from 'vultus-antd';

// A loading site's spinner. When the app lends a Spinner through vultus-core's
// slot (vultus-antd's VultusProvider components={{ Spinner }}), this renders
// that Spinner, keyed `localKey` within any enclosing SpinnerScope (each agent
// view scopes its conversation with optio:process:<id>); the lent Spinner wins
// over `fallback` (the working indicator's agent-brand nativeSpinner).
// Otherwise it renders `fallback`, else antd's Spin: exactly what the site
// rendered before the slot existed. `size` (default 'small') goes to the
// lent Spinner and antd's Spin; the working indicator asks for 'large'.
export function SlotSpinner({ localKey, fallback, size = 'small' }: {
  localKey: string; fallback?: React.ReactNode; size?: 'small' | 'default' | 'large';
}): React.ReactNode {
  const { Spinner } = useVultusComponents();
  const spinnerKey = useSpinnerKey(localKey);
  if (Spinner) return <Spinner spinnerKey={spinnerKey} size={size} />;
  return fallback ?? <Spin size={size} />;
}
