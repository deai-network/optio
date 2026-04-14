import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthUIProvider } from '@daveyplate/better-auth-ui';
import { authClient } from './auth-client.js';
import './i18n.js';
import './auth.css';
import App from './App.js';

const queryClient = new QueryClient();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthUIProvider
        authClient={authClient}
        navigate={(href) => { window.location.href = href; }}
        Link={({ href, children, ...props }: { href: string; children: React.ReactNode; [key: string]: any }) => (
          <a href={href} {...props}>{children}</a>
        )}
      >
        <App />
      </AuthUIProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
