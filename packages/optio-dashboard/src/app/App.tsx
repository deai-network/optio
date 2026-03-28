import { useState } from 'react';
import { Layout, Typography } from 'antd';
import {
  OptioProvider,
  ProcessList,
  ProcessTreeView,
  ProcessLogPanel,
  useProcessActions,
  useProcessStream,
  useProcessListStream,
} from 'optio-ui';

const { Header, Sider, Content } = Layout;
const { Title } = Typography;

const prefix = (window as any).__OPTIO_PREFIX__ || 'optio';

function Dashboard() {
  const [selectedProcessId, setSelectedProcessId] = useState<string | null>(null);
  const { processes, connected: listConnected } = useProcessListStream();
  const { tree, logs, connected: treeConnected } = useProcessStream(
    selectedProcessId ?? undefined,
  );
  const { launch, cancel, dismiss } = useProcessActions();

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', padding: '0 24px' }}>
        <Title level={4} style={{ color: '#fff', margin: 0 }}>Optio Dashboard</Title>
      </Header>
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
    </Layout>
  );
}

export default function App() {
  return (
    <OptioProvider prefix={prefix}>
      <Dashboard />
    </OptioProvider>
  );
}
