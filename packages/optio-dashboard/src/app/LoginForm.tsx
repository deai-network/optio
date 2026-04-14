import { useState } from 'react';
import { Form, Input, Button, Typography, Card } from 'antd';
import { authClient } from './auth-client.js';

const ADMIN_EMAIL = 'admin@optio.local';

export function LoginForm() {
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onFinish({ password }: { password: string }) {
    setError(null);
    setLoading(true);
    const { error } = await authClient.signIn.email({ email: ADMIN_EMAIL, password });
    setLoading(false);
    if (error) {
      setError(error.message ?? 'Sign in failed');
    }
  }

  return (
    <Card style={{ width: '100%' }}>
      <Typography.Title level={4} style={{ marginTop: 0, marginBottom: 24 }}>
        Optio Dashboard
      </Typography.Title>
      <Form layout="vertical" onFinish={onFinish}>
        <Form.Item
          label="Password"
          name="password"
          rules={[{ required: true, message: 'Please enter your password' }]}
          validateStatus={error ? 'error' : undefined}
          help={error ?? undefined}
        >
          <Input.Password placeholder="Password" autoFocus />
        </Form.Item>
        <Form.Item style={{ marginBottom: 0 }}>
          <Button type="primary" htmlType="submit" loading={loading} block>
            Sign in
          </Button>
        </Form.Item>
      </Form>
    </Card>
  );
}
