import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from 'antd';
import './i18n.js';
import App from './App.js';
import { DaeoRoot, styleMode } from './styleMode.js';

const queryClient = new QueryClient();

async function start() {
  const app = (
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  );
  // Ant Design X experiment: the Daeo look renders inside daeo-style's vultus
  // bridge, loaded only then (styleMode.tsx).
  const root = styleMode === 'daeo'
    ? <DaeoRoot Provider={(await import('@deai-network/daeo-style-for-vultus')).DaeoStyleProvider}>{app}</DaeoRoot>
    : (
      // Tags keep antd 5's outlined look: antd 6's default borderless
      // "filled" tags make light status badges read like plain text.
      <ConfigProvider tag={{ variant: 'outlined' }}>{app}</ConfigProvider>
    );
  ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode>{root}</React.StrictMode>);
}

void start();
