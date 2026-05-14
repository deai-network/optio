import { Tag, Typography } from 'antd';
import { logs, processIndex } from '../fixtures.js';

const { Text } = Typography;

const LEVEL_COLORS: Record<string, string> = {
  event: 'cyan', info: 'blue', debug: 'default', warning: 'gold', error: 'red',
};

export function ColoredTag() {
  return (
    <div>
      <h3>Variant 1 — Color-coded process tag</h3>
      <p style={{ color: '#999' }}>
        Stable color per process. Identity = colored tag + label. No depth cue.
      </p>
      <div style={panelStyle}>
        {logs.map((entry, idx) => {
          const info = processIndex[entry.processId];
          return (
            <div key={idx} style={rowStyle}>
              <Text type="secondary" style={tsStyle}>{new Date(entry.timestamp).toLocaleTimeString()}</Text>
              <Tag color={LEVEL_COLORS[entry.level] ?? 'default'} style={tagStyle}>{entry.level.toUpperCase()}</Tag>
              <span style={{
                background: info.color,
                color: '#000',
                padding: '0 6px',
                borderRadius: 3,
                fontSize: 11,
                fontWeight: 600,
                whiteSpace: 'nowrap',
              }}>
                {info.label}
              </span>
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
