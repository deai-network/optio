import { ProcessLogPanel } from '../../../../../optio-ui/src/components/ProcessLogPanel.js';
import { logs, tree } from '../fixtures.js';

export function Baseline() {
  return (
    <div>
      <h3>Baseline — current ProcessLogPanel</h3>
      <p style={{ color: '#999' }}>
        Real panel rendered with fixture tree + interleaved logs from 6 processes.
      </p>
      <ProcessLogPanel logs={logs as any} tree={tree as any} />
    </div>
  );
}
