import { useState } from 'react';
import { Alert, Button, Layout, Select, Typography } from 'antd';
import {
  OptioProvider,
  WithFilteredProcesses,
  ProcessFilters,
  FilteredProcessList,
  ProcessTreeView,
  ProcessLogPanel,
  useProcessActions,
  useProcessStream,
  useProcessListStream,
  useInstances,
  useOptioLive,
} from 'optio-ui';
import { LoginForm } from './LoginForm.js';
import { useSession, signOut } from './auth-client.js';

const { Header, Sider, Content } = Layout;
const { Title } = Typography;

function Dashboard() {
  const [selectedProcessId, setSelectedProcessId] = useState<string | null>(null);
  const { processes, connected: listConnected } = useProcessListStream();
  const { tree, logs, connected: treeConnected } = useProcessStream(
    selectedProcessId ?? undefined,
  );
  const { launch, cancel, dismiss } = useProcessActions();
  const live = useOptioLive();

  return (
    <WithFilteredProcesses>
      <Layout>
        <Layout>
          <Sider width={400} style={{ background: '#fff', overflow: 'auto' }}>
            <ProcessFilters />
            <FilteredProcessList
              processes={processes}
              loading={!listConnected}
              onLaunch={live ? launch : undefined}
              onCancel={live ? cancel : undefined}
              onProcessClick={setSelectedProcessId}
            />
          </Sider>
          <Content style={{ padding: '24px', overflow: 'auto' }}>
            {selectedProcessId ? (
              <>
                <ProcessTreeView
                  treeData={tree}
                  sseState={{ connected: treeConnected }}
                  onCancel={live ? cancel : undefined}
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
      </Layout>
    </WithFilteredProcesses>
  );
}

function instanceKey(inst: { database: string; prefix: string }) {
  return `${inst.database}/${inst.prefix}`;
}

function AppContent() {
  const { instances, isLoading, refetch } = useInstances();
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  if (isLoading) return null;

  const selected = instances.find((i) => instanceKey(i) === selectedKey) ?? instances[0] ?? null;

  const liveInstances = instances.filter((i) => i.live);
  const offlineInstances = instances.filter((i) => !i.live);

  const options: any[] = [];
  for (const inst of liveInstances) {
    options.push({ label: instanceKey(inst), value: instanceKey(inst) });
  }
  if (liveInstances.length > 0 && offlineInstances.length > 0) {
    options.push({ label: '───', value: '__separator__', disabled: true });
  }
  for (const inst of offlineInstances) {
    options.push({ label: `${instanceKey(inst)} (offline)`, value: instanceKey(inst) });
  }

  const headerRight = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      {instances.length > 0 && (
        <>
          <Select
            style={{ minWidth: 200 }}
            value={selected ? instanceKey(selected) : undefined}
            placeholder="Select instance"
            options={options}
            onChange={setSelectedKey}
          />
          <Button onClick={() => refetch()}>Refresh</Button>
        </>
      )}
      <Button onClick={() => signOut()}>Sign out</Button>
    </div>
  );

  if (!selected) {
    return (
      <Layout style={{ minHeight: '100vh' }}>
        <Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 24px' }}>
          <Title level={4} style={{ color: '#fff', margin: 0 }}>Optio Dashboard</Title>
          {headerRight}
        </Header>
        <Alert
          type="info"
          message="No optio instance detected in the database"
          style={{ margin: 24 }}
        />
      </Layout>
    );
  }

  return (
    <OptioProvider prefix={selected.prefix} database={selected.database} live={selected.live}>
      <Layout style={{ minHeight: '100vh' }}>
        <Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 24px' }}>
          <Title level={4} style={{ color: '#fff', margin: 0 }}>Optio Dashboard</Title>
          {headerRight}
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
