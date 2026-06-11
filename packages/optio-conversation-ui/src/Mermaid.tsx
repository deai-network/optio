import { useEffect, useRef, useState } from 'react';
import { theme } from 'antd';

// Render a ```mermaid fenced block as an SVG diagram.
//
// mermaid is large, so it is dynamically imported — it stays out of the main
// bundle and only loads when a diagram actually appears. While an assistant
// answer is still streaming, the fenced block arrives incrementally and is not
// yet valid mermaid; render() throws, and we fall back to showing the raw
// source until the block completes and parses cleanly.

let seq = 0;

// Rendered-SVG cache keyed by chart source. The dashboard re-renders the
// widget on every process poll (~1s); without this cache the Mermaid subtree
// would re-run its async render each time, flashing the source fallback then
// the diagram on a loop. Seeding state from the cache makes a re-mount show
// the finished diagram synchronously, so a known chart never reverts.
const svgCache = new Map<string, string>();

export function Mermaid({ chart }: { chart: string }) {
  const { token } = theme.useToken();
  const [svg, setSvg] = useState(() => svgCache.get(chart) ?? '');
  const idRef = useRef(`mmd-${seq++}`);

  useEffect(() => {
    const cached = svgCache.get(chart);
    if (cached) {
      setSvg(cached);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const mermaid = (await import('mermaid')).default;
        // securityLevel "strict" sanitizes diagram text/labels; startOnLoad
        // off because we drive rendering ourselves. initialize is idempotent.
        mermaid.initialize({ startOnLoad: false, securityLevel: 'strict' });
        const { svg } = await mermaid.render(idRef.current, chart);
        if (!cancelled) {
          svgCache.set(chart, svg);
          setSvg(svg);
        }
      } catch {
        // Incomplete (streaming) or invalid mermaid — keep showing the source
        // fallback below until a later attempt parses cleanly.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [chart]);

  if (svg) {
    // mermaid output is generated from the chart text under securityLevel:strict.
    // Once we have an SVG it wins, even if a later parse attempt fails.
    return <div style={{ margin: '4px 0' }} dangerouslySetInnerHTML={{ __html: svg }} />;
  }
  {
    // Loading / streaming / parse failure: show the source so the operator
    // sees progress.
    return (
      <pre
        style={{
          background: token.colorFillQuaternary,
          border: `1px solid ${token.colorBorderSecondary}`,
          borderRadius: 6,
          padding: 8,
          overflowX: 'auto',
          margin: '4px 0',
          fontSize: 12,
        }}
      >
        {chart}
      </pre>
    );
  }
}
