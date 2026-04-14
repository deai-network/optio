import { useState } from 'react';
import { Layout, Typography, Select, Alert, Button } from 'antd';
import { LoginForm } from './LoginForm.js';
import { useSession, signOut } from './auth-client.js';
import {
  OptioProvider,
  ProcessList,
  ProcessTreeView,
  ProcessLogPanel,
  useProcessActions,
  useProcessStream,
  useProcessListStream,
  usePrefixes,
} from 'optio-ui';

const { Header, Sider, Content } = Layout;
const { Title } = Typography;

function PrefixSelector({ onSelect }: { onSelect: (prefix: string) => void }) {
  const { prefixes, isLoading, error } = usePrefixes();

  if (isLoading) return null;
  if (error) return <Alert type="error" message="Failed to detect prefixes" />;
  if (prefixes.length === 0) {
    return <Alert type="info" message="No optio instance detected in the database" />;
  }

  return (
    <div style={{ padding: 24 }}>
      <Typography.Text>Multiple optio instances detected. Select one:</Typography.Text>
      <Select
        style={{ width: '100%', marginTop: 8 }}
        placeholder="Select prefix"
        options={prefixes.map((p) => ({ label: p, value: p }))}
        onChange={onSelect}
      />
    </div>
  );
}

function Dashboard() {
  const [selectedProcessId, setSelectedProcessId] = useState<string | null>(null);
  const { processes, connected: listConnected } = useProcessListStream();
  const { tree, logs, connected: treeConnected } = useProcessStream(
    selectedProcessId ?? undefined,
  );
  const { launch, cancel, dismiss } = useProcessActions();

  return (
    <Layout>
      <Sider width={400} style={{ background: '#fff', overflow: 'auto' }}>
        <ProcessList
          processes={processes}
          loading={!listConnected}
          onLaunch={launch}
          onCancel={cancel}
          onProcessClick={setSelectedProcessId}
        />
      </Sider>
      <Content style={{ padding: '24px', overflow: 'auto' }}>
        {selectedProcessId ? (
          <>
            <ProcessTreeView
              treeData={tree}
              sseState={{ connected: treeConnected }}
              onCancel={cancel}
            />
            <ProcessLogPanel logs={logs} />
          </>
        ) : (
          <div style={{ color: '#999', textAlign: 'center', marginTop: 100 }}>
            Select a process to view details
          </div>
        )}
      </Content>
    </Layout>
  );
}

function AppContent() {
  const { prefixes, isLoading } = usePrefixes();
  const [manualPrefix, setManualPrefix] = useState<string | null>(null);

  if (isLoading) return null;

  // Multiple prefixes and user hasn't picked yet
  if (prefixes.length > 1 && !manualPrefix) {
    return (
      <Layout style={{ minHeight: '100vh' }}>
        <Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 24px' }}>
          <Title level={4} style={{ color: '#fff', margin: 0 }}>Optio Dashboard</Title>
          <Button onClick={() => signOut()}>Sign out</Button>
        </Header>
        <PrefixSelector onSelect={setManualPrefix} />
      </Layout>
    );
  }

  // Zero prefixes
  if (prefixes.length === 0) {
    return (
      <Layout style={{ minHeight: '100vh' }}>
        <Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 24px' }}>
          <Title level={4} style={{ color: '#fff', margin: 0 }}>Optio Dashboard</Title>
          <Button onClick={() => signOut()}>Sign out</Button>
        </Header>
        <Alert
          type="info"
          message="No optio instance detected in the database"
          style={{ margin: 24 }}
        />
      </Layout>
    );
  }

  const prefix = manualPrefix ?? prefixes[0];

  return (
    <OptioProvider prefix={prefix}>
      <Layout style={{ minHeight: '100vh' }}>
        <Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 24px' }}>
          <Title level={4} style={{ color: '#fff', margin: 0 }}>Optio Dashboard</Title>
          <Button onClick={() => signOut()}>Sign out</Button>
        </Header>
        <Dashboard />
      </Layout>
    </OptioProvider>
  );
}

export default function App() {
  const { data: session, isPending } = useSession();

  if (isPending) return null;

  if (!session) {
    return (
      <div style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        minHeight: '100vh',
      }}>
        <div style={{ width: '100%', maxWidth: 400, padding: '0 16px' }}>
          <LoginForm />
        </div>
      </div>
    );
  }

  return (
    <OptioProvider>
      <AppContent />
    </OptioProvider>
  );
}
