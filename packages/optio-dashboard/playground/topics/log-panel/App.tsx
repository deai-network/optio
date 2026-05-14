import { Baseline } from './variants/baseline.js';
import { ColoredTag } from './variants/colored-tag.js';
import { PathText } from './variants/path.js';
import { IndentBar } from './variants/indent-bar.js';

export function App() {
  return (
    <div>
      <h2 style={{ marginTop: 0 }}>ProcessLogPanel — disambiguation variants</h2>
      <p style={{ color: '#999' }}>
        Same fixtures across all panels. 6-process tree, ~40 interleaved log entries.
        Scroll any panel independently.
      </p>
      <div style={{ display: 'grid', gap: 24 }}>
        <Baseline />
        <ColoredTag />
        <PathText />
        <IndentBar />
      </div>
    </div>
  );
}
