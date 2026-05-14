import React, { useEffect, useState } from 'react';
import ReactDOM from 'react-dom/client';
import { ConfigProvider, theme } from 'antd';
import './i18n.js';
import logPanel from './topics/log-panel/index.js';

interface Topic {
  name: string;
  slug: string;
  App: React.ComponentType;
}

const TOPICS: Topic[] = [logPanel];

function currentSlug(): string {
  const hash = window.location.hash.replace(/^#/, '');
  return TOPICS.some((t) => t.slug === hash) ? hash : TOPICS[0].slug;
}

function App() {
  const [slug, setSlug] = useState<string>(currentSlug());

  useEffect(() => {
    const handler = () => setSlug(currentSlug());
    window.addEventListener('hashchange', handler);
    return () => window.removeEventListener('hashchange', handler);
  }, []);

  const topic = TOPICS.find((t) => t.slug === slug) ?? TOPICS[0];
  const Current = topic.App;

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <nav
        style={{
          width: 220,
          borderRight: '1px solid #303030',
          padding: 16,
          flex: '0 0 auto',
        }}
      >
        <strong style={{ display: 'block', marginBottom: 12 }}>Topics</strong>
        {TOPICS.map((t) => (
          <a
            key={t.slug}
            href={`#${t.slug}`}
            style={{
              display: 'block',
              padding: '6px 8px',
              marginBottom: 4,
              borderRadius: 4,
              textDecoration: 'none',
              color: t.slug === slug ? '#fff' : '#69c0ff',
              background: t.slug === slug ? '#177ddc' : 'transparent',
            }}
          >
            {t.name}
          </a>
        ))}
      </nav>
      <main style={{ flex: 1, padding: 16 }}>
        <Current />
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider theme={{ algorithm: theme.darkAlgorithm }}>
      <App />
    </ConfigProvider>
  </React.StrictMode>,
);
