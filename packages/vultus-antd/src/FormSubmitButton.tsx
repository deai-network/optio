import { Button, type FormInstance } from 'antd';
import { useActionErrorCtx, type ActionStatus } from 'vultus-core';

interface Props<TArgs> {
  action: ActionStatus<TArgs>;
  size?: 'small' | 'middle' | 'large';
  block?: boolean;
}

export function FormSubmitButton<TArgs>({ action, size, block }: Props<TArgs>) {
  const ctx = useActionErrorCtx();

  if (action.invisible) return null;

  const handleClick = async () => {
    if (action.disabled || action.pending) return;
    if (!ctx?.form) {
      console.error(`FormSubmitButton '${action.id}': not inside a <FormPanel>`);
      return;
    }
    try {
      // ctx.form is core's minimal FormLike; the runtime value is a real antd
      // FormInstance (supplied by <FormPanel>), so recover validateFields here.
      const values = await (ctx.form as FormInstance).validateFields();
      // FormSubmitButton always passes a value; the conditional rest
      // tuple in ActionStatus's firePromise signature can't see through
      // the generic, so we cast to the simple (args) → Promise<void>.
      const fire = action.firePromise as (args: TArgs) => Promise<void>;
      await fire(values as TArgs);
    } catch {
      // antd's validateFields rejects on validation failure;
      // field-level errors are already rendered by antd.
    }
  };

  return (
    <Button
      type={action.variant === 'primary' ? 'primary' : 'default'}
      danger={action.variant === 'danger'}
      icon={action.icon}
      size={size}
      block={block}
      loading={action.pending}
      disabled={action.disabled || action.pending}
      onClick={handleClick}
      title={action.reason}
      data-action-id={action.id}
    >
      {action.label}
    </Button>
  );
}
