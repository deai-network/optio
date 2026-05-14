import { Tag, Typography } from 'antd';
import { logs, processIndex } from '../fixtures.js';

const { Text } = Typography;

const LEVEL_COLORS: Record<string, string> = {
  event: 'cyan', info: 'blue', debug: 'default', warning: 'gold', error: 'red',
};

export function PathText() {
  return (
    <div>
      <h3>Variant 2 — Path text</h3>
      <p style={{ color: '#999' }}>
        Full ancestor chain inline. Identity + position bundled. Wider rows.
      </p>
      <div style={panelStyle}>
        {logs.map((entry, idx) => {
          const info = processIndex[entry.processId];
          return (
            <div key={idx} style={rowStyle}>
              <Text type="secondary" style={tsStyle}>{new Date(entry.timestamp).toLocaleTimeString()}</Text>
              <Tag color={LEVEL_COLORS[entry.level] ?? 'default'} style={tagStyle}>{entry.level.toUpperCase()}</Tag>
              <Text type="secondary" style={{ fontSize: 11, whiteSpace: 'nowrap' }}>
                {info.ancestors.map((label, i) => (
                  <span key={i}>
                    {i > 0 && <span style={{ color: '#555', padding: '0 4px' }}>›</span>}
                    <span style={i === info.ancestors.length - 1 ? { color: '#e6e6e6', fontWeight: 600 } : undefined}>
                      {label}
                    </span>
                  </span>
                ))}
              </Text>
              <Text>{entry.message}</Text>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const panelStyle: React.CSSProperties = {
  maxHeight: 400, overflow: 'auto', border: '1px solid #303030', borderRadius: 4,
  padding: 8, fontFamily: 'monospace', fontSize: 12,
};
const rowStyle: React.CSSProperties = { display: 'flex', gap: 8, marginBottom: 4, alignItems: 'baseline' };
const tsStyle: React.CSSProperties = { whiteSpace: 'nowrap', fontSize: 11 };
const tagStyle: React.CSSProperties = { fontSize: 10 };
