import { Tag, Typography } from 'antd';
import { logs, processIndex } from '../fixtures.js';

const { Text } = Typography;

const LEVEL_COLORS: Record<string, string> = {
  event: 'cyan', info: 'blue', debug: 'default', warning: 'gold', error: 'red',
};

const INDENT_PX = 20;

export function IndentBar() {
  return (
    <div>
      <h3>Variant 3 — Indent + colored left bar</h3>
      <p style={{ color: '#999' }}>
        Depth = horizontal indent. Colored left bar = process identity. Label only on
        transition (when previous row was a different process).
      </p>
      <div style={panelStyle}>
        {logs.map((entry, idx) => {
          const info = processIndex[entry.processId];
          const prev = idx > 0 ? logs[idx - 1] : null;
          const transition = !prev || prev.processId !== entry.processId;
          return (
            <div
              key={idx}
              style={{
                display: 'flex',
                alignItems: 'baseline',
                marginBottom: 2,
                paddingLeft: info.depth * INDENT_PX,
              }}
            >
              <div style={{
                width: 3,
                alignSelf: 'stretch',
                background: info.color,
                marginRight: 8,
                flex: '0 0 auto',
              }} />
              <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flex: 1, minWidth: 0 }}>
                <Text type="secondary" style={tsStyle}>{new Date(entry.timestamp).toLocaleTimeString()}</Text>
                <Tag color={LEVEL_COLORS[entry.level] ?? 'default'} style={tagStyle}>{entry.level.toUpperCase()}</Tag>
                {transition && (
                  <Text style={{ color: info.color, fontSize: 11, fontWeight: 600, whiteSpace: 'nowrap' }}>
                    {info.label}
                  </Text>
                )}
                <Text>{entry.message}</Text>
              </div>
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
const tsStyle: React.CSSProperties = { whiteSpace: 'nowrap', fontSize: 11 };
const tagStyle: React.CSSProperties = { fontSize: 10 };
