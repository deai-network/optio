import type { CSSProperties } from 'react';

// On-brand "agent is working" spinners, one per engine. Rendered by
// ConversationView in place of the generic antd <Spin> when a task opts in via
// the native_spinner option (conversation mode only; see each wrapper's
// TaskConfig). Each spinner is self-contained: size-accepting, driven by
// currentColor where the brand mark is monochrome, and reduced-motion aware.
//
// Fidelity: kimicode + opencode are extracted from those agents' own web UIs
// (MoonSpinner.vue; packages/ui spinner.tsx); claudecode is the real claude.ai
// asset (APNG); codex is ported from codex-rs (tui/src/shimmer.rs); antigravity
// is the braille "dots" run from the agy CLI binary. cursor (translucent cube)
// and grok (3x3 snake matrix) are built to the brand's own spec/geometry.
// Retuning a mark is a local edit here; it never touches the wiring.

export type SpinnerEngine =
  | 'claudecode' | 'opencode' | 'grok' | 'codex' | 'cursor' | 'kimicode' | 'antigravity';

// @keyframes can't be expressed inline, so (like ConversationView's flash/copy
// styles) inject them once into the document head, keyed by a stable id.
const STYLE_ID = 'optio-native-spinner-style';
function ensureSpinnerStyle(): void {
  if (typeof document === 'undefined' || document.getElementById(STYLE_ID)) return;
  const el = document.createElement('style');
  el.id = STYLE_ID;
  el.textContent = `
  .optio-sp-claude img { display:block; width:100%; height:100%; }
  .optio-sp-claude .still { display:none; }
  @media (prefers-reduced-motion: reduce) {
    .optio-sp-claude .anim { display:none; }
    .optio-sp-claude .still { display:block; }
  }

  @keyframes optio-sp-oc { 0%,100%{opacity:.15;} 50%{opacity:1;} }
  @keyframes optio-sp-oc-dim { 0%,100%{opacity:.08;} 50%{opacity:.5;} }
  .optio-sp-oc rect { animation-name: optio-sp-oc; animation-timing-function: ease-in-out; animation-iteration-count: infinite; animation-fill-mode: both; }
  .optio-sp-oc rect.dim { animation-name: optio-sp-oc-dim; }

  @keyframes optio-sp-grok-02 { 0%{opacity:1} 7.1%{opacity:.7} 14.3%{opacity:.45} 21.4%{opacity:.25} 28.6%{opacity:.1} 35.7%,92.9%{opacity:0} 100%{opacity:1} }
  @keyframes optio-sp-grok-01 { 0%{opacity:0} 7.1%{opacity:1} 14.3%{opacity:.7} 21.4%{opacity:.45} 28.6%{opacity:.25} 35.7%{opacity:.1} 42.9%,100%{opacity:0} }
  @keyframes optio-sp-grok-00 { 0%,7.1%{opacity:0} 14.3%{opacity:1} 21.4%{opacity:.7} 28.6%{opacity:.45} 35.7%{opacity:.25} 42.9%{opacity:.1} 50%,100%{opacity:0} }
  @keyframes optio-sp-grok-10 { 0%,14.3%{opacity:0} 21.4%{opacity:1} 28.6%{opacity:.7} 35.7%{opacity:.45} 42.9%{opacity:.25} 50%{opacity:.1} 57.1%,100%{opacity:0} }
  @keyframes optio-sp-grok-20 { 0%,21.4%{opacity:0} 28.6%{opacity:1} 35.7%{opacity:.7} 42.9%{opacity:.45} 50%{opacity:.25} 57.1%{opacity:.1} 64.3%,100%{opacity:0} }
  @keyframes optio-sp-grok-21 { 0%,28.6%{opacity:0} 35.7%{opacity:1} 42.9%{opacity:.7} 50%{opacity:.45} 57.1%{opacity:.25} 64.3%{opacity:.1} 71.4%,100%{opacity:0} }
  @keyframes optio-sp-grok-22 { 0%,35.7%{opacity:0} 42.9%{opacity:1} 50%{opacity:.7} 57.1%{opacity:.45} 64.3%{opacity:.25} 71.4%{opacity:.1} 78.6%,100%{opacity:0} }
  @keyframes optio-sp-grok-12 { 0%,42.9%{opacity:0} 50%{opacity:1} 57.1%{opacity:.7} 64.3%{opacity:.45} 71.4%{opacity:.25} 78.6%{opacity:.1} 85.7%,100%{opacity:0} }
  @keyframes optio-sp-grok-11 { 0%,50%{opacity:0} 57.1%{opacity:1} 64.3%{opacity:.7} 71.4%{opacity:.45} 78.6%{opacity:.25} 85.7%{opacity:.1} 92.9%,100%{opacity:0} }
  .optio-sp-grok .lit { animation-duration:2s; animation-timing-function:linear; animation-iteration-count:infinite; }

  @keyframes optio-sp-codex { 0%,26%,100%{opacity:.26;} 10%{opacity:1;} }
  .optio-sp-codex { display:inline-flex; align-items:center; justify-content:center; }
  .optio-sp-codex i { display:block; border-radius:50%; background:currentColor; opacity:.26; animation: optio-sp-codex 2s ease-in-out infinite; }

  @keyframes optio-sp-cursor { 0%,100%{opacity:1;} 50%{opacity:.06;} }
  .optio-sp-cursor .pulse { animation: optio-sp-cursor 1.5s ease-in-out infinite; }

  @keyframes optio-sp-kimi { 0%,12.49%{opacity:1;} 12.5%,100%{opacity:0;} }
  .optio-sp-kimi span { position:absolute; inset:0; text-align:center; opacity:0; animation: optio-sp-kimi 960ms steps(1,end) infinite; }

  @keyframes optio-sp-agy { 0%,9.99%{opacity:1;} 10%,100%{opacity:0;} }
  .optio-sp-agy span { position:absolute; inset:0; text-align:center; opacity:0; animation: optio-sp-agy 800ms steps(1,end) infinite; }

  @media (prefers-reduced-motion: reduce) {
    .optio-sp-grok .lit, .optio-sp-codex i, .optio-sp-oc rect, .optio-sp-cursor .pulse, .optio-sp-kimi span, .optio-sp-agy span { animation: none !important; }
    .optio-sp-grok .lit{ opacity:0; }
    .optio-sp-kimi span:nth-child(5){ opacity:1; }
    .optio-sp-agy span:nth-child(1){ opacity:1; }
    .optio-sp-codex i{ opacity:.55; }
  }`;
  document.head.appendChild(el);
}

// --- claudecode: the real claude.ai "generating" mark — a hand-drawn Crail
// starburst rotating over 8 frames. Extracted from the claude.ai asset (a
// 700x50 strip), cropped to the 50x50 mark, cream background removed with a
// SOFT alpha key (edge pixels keep partial transparency) and the strokes
// recoloured to pure Crail #D97757 so it stays clean on any ground. Encoded as
// an animated PNG (APNG) — GIF's 1-bit transparency re-jaggs the soft edges;
// APNG's 8-bit alpha preserves them. Two data URIs (APNG + a still frame) keep
// it self-contained; the still is swapped in under prefers-reduced-motion.
const CLAUDE_APNG =
  'data:image/png;base64,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';
const CLAUDE_STILL =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADIAAAAyCAMAAAAp4XiDAAAAIGNIUk0AAHomAACAhAAA+gAAAIDoAAB1MAAA6mAAADqYAAAXcJy6UTwAAAFWUExURdl3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3V9l3VwD/ALjq0NUAAABwdFJOUwABMHECKfub7c7SNCdPGUvnBCB6CP126g8Fy0T8phyxilAyXGdIWTXlbyQW+UqHvLeVGpTg49HZxDfV7/pmRVVA3KgSwIRzam3HgSKv6a0dY6qFKwzzC7tSo6Dfq4CR8bQ5M1e+YG47nNb3To8XPjbNzxjYAAAAAWJLR0Rxrwdc4gAAAAd0SU1FB+oHBRYdA4A7XAIAAAItSURBVEjH7ZTrWxJBFMYPqLEaokAioRIYbVIUiQVEFzO8EHZPyLyUXc2u7///qXNmF0HcGbavPZ3n2d0zs+9vzpmZM0PkYQH698znnIJDwx135EzIF2JhtOOOAWd9EGEA467PbsQHMsG6yahyo+wO+0BirENcueeAKbc3MW0l9UxImPMuner2zRjizPL/OXHSwAXVk5FRTKllRTDPzryb4UXpyBmnk4KT0SXA5s9lIRbMKxCEk3oeuEJ0tSDNawNWLeQMex3g1R4VoqjVdmpKVDbdkDkvio+SMURyKU/jKsxN3KKcIoylFqAyUJmpsu42UJtQRCHZ89/D7ohoih/G7ioC99xf95fDnnEeFNBnK9wbth9WxI96MvXVPiS+dtxj6Sa0Dp3Zp8XpjUausdEoeuofNTc9xn+sGf3J0+wzTUoZL30qUTfty/PF8sIpZrL8Yu5l015+taXlYtO66Vc1+984KVu1Wt1G2wtpv+5fKNmrke31N8Xayo4X4VZVL7NLRttTov3miTiW6TbbEsVbsl2tVGftHYyXZqBqHcToPYtaUv8fdrmak7K/H83JqbO/+Ylf6RK/PlO85wB4Wl3SyZJEIfrCYYgO2f9qQES7T8TbecStA2CIqPQNGNMjcC4UPpF7/EkCs3yuAlzch1okhaO2Q+al+R34Id9EJawPo26gn4z8Us2lQXeSs9ZEv3F8c7ewMxhh2+7ON5LxEcWZ9povYY9Fgn9L/Ded/QG55Peft8j3+AAAACV0RVh0ZGF0ZTpjcmVhdGUAMjAyNi0wNy0wNVQyMjoyODo1MSswMDowMGoU5ywAAAAldEVYdGRhdGU6bW9kaWZ5ADIwMjYtMDctMDVUMjI6Mjg6NTErMDA6MDAbSV+QAAAAKHRFWHRkYXRlOnRpbWVzdGFtcAAyMDI2LTA3LTA1VDIyOjI5OjAzKzAwOjAwfOEKPAAAAABJRU5ErkJggg==';
function ClaudeSpinner({ size }: { size: number }) {
  return (
    <span className="optio-sp-claude" style={{ display: 'inline-block', width: size, height: size }}>
      <img className="anim" src={CLAUDE_APNG} alt="" width={size} height={size} />
      <img className="still" src={CLAUDE_STILL} alt="" width={size} height={size} />
    </span>
  );
}

// --- opencode: 4x4 grid of pulsing rounded squares (inner bright, outer dim) --
const OC_OUTER = new Set([1, 2, 4, 7, 8, 11, 13, 14]);
const OC_CORNER = new Set([0, 3, 12, 15]);
function OpencodeSpinner({ size }: { size: number }) {
  return (
    <svg className="optio-sp-oc" viewBox="0 0 15 15" fill="currentColor" width={size} height={size} aria-hidden="true">
      {Array.from({ length: 16 }, (_, i) => {
        const x = (i % 4) * 4, y = Math.floor(i / 4) * 4;
        if (OC_CORNER.has(i)) return <rect key={i} x={x} y={y} width="3" height="3" rx="1" style={{ opacity: 0 }} />;
        // Deterministic pseudo-stagger (no Math.random — stable across renders/SSR).
        const delay = ((i * 173) % 150) / 100, dur = 1 + ((i * 97) % 100) / 100;
        return (
          <rect key={i} className={OC_OUTER.has(i) ? 'dim' : ''} x={x} y={y} width="3" height="3" rx="1"
            style={{ animationDelay: `${delay}s`, animationDuration: `${dur}s` }} />
        );
      })}
    </svg>
  );
}

// --- grok: a 3x3 node matrix with a snake-like light trail. Nodes sit dark
// grey; a ~5-node bright trail sweeps a spiral perimeter path into the centre
// and vanishes: (0,2)→(0,1)→(0,0)→(1,0)→(2,0)→(2,1)→(2,2)→(1,2)→(1,1). Each
// node is a grey base rect with a white overlay whose opacity is driven by a
// per-node keyframe (peak as the head passes, fading over the trail).
const GROK_CELLS = ([] as Array<{ r: number; c: number; x: number; y: number }>);
for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) GROK_CELLS.push({ r, c, x: c * 9.5, y: r * 9.5 });
function GrokSpinner({ size }: { size: number }) {
  return (
    <span className="optio-sp-grok" style={{ display: 'inline-flex', lineHeight: 0, width: size, height: size }}>
      <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
        {GROK_CELLS.map(({ r, c, x, y }) => (
          <rect key={`b${r}${c}`} x={x} y={y} width={5} height={5} rx={1.3} fill="#3a3a3a" />
        ))}
        {GROK_CELLS.map(({ r, c, x, y }) => (
          <rect key={`l${r}${c}`} className="lit" x={x} y={y} width={5} height={5} rx={1.3}
            fill="#f2f2f2" style={{ animationName: `optio-sp-grok-${r}${c}` }} />
        ))}
      </svg>
    </span>
  );
}

// --- codex: shimmer sweep over a row of bullets — ported from codex-rs
// (tui/src/shimmer.rs): a raised-cosine highlight band travels the glyphs on a
// 2s loop (sweep_seconds=2.0) with padding either side, so it reads as sweep-
// then-pause. Codex renders it over its status text/bullet; here it's a compact
// 5-dot row. Monochrome via currentColor (codex's default fg). The per-dot
// negative delays stagger the band so the highlight travels across the row.
const CODEX_DOTS = 5;
function CodexSpinner({ size }: { size: number }) {
  const dot = Math.max(2, Math.round(size * 0.16));
  const gap = Math.max(1, Math.round(size * 0.08));
  return (
    <span className="optio-sp-codex" style={{ gap, width: size, height: size }}>
      {Array.from({ length: CODEX_DOTS }, (_, i) => (
        <i key={i} style={{ width: dot, height: dot, animationDelay: `${-(i * 0.13)}s` }} />
      ))}
    </span>
  );
}

// --- cursor: the Cursor logo — a translucent isometric cube. Near corner at
// centre (C); front edges C→UL, C→UR, C→B; the three faint back edges (C→T
// vertical, C→LL, C→LR) show through the glass. Metallic gradient faces. The
// two bright facets pulse OPACITY toward zero, so each "disappears" and reveals
// the back edges behind it: (1) front half of the top face (UL,C,UR) and
// (2) the top-left half of the right face (C,UR,B) — the right face is cut on
// the C-side diagonal (top-left / bottom-right). Vertices in a 24 viewBox:
// T(12,2) UL(3.2,7) UR(20.8,7) C(12,12) LL(3.2,17) LR(20.8,17) B(12,22).
function CursorSpinner({ size }: { size: number }) {
  return (
    <span className="optio-sp-cursor" style={{ display: 'inline-flex', lineHeight: 0, width: size, height: size }}>
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <defs>
          <linearGradient id="oc-cur-tb" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stopColor="#7a7a7a" /><stop offset="1" stopColor="#161616" /></linearGradient>
          <linearGradient id="oc-cur-lu" x1="0.5" y1="0" x2="0.2" y2="1"><stop offset="0" stopColor="#e2e2e2" /><stop offset="1" stopColor="#333333" /></linearGradient>
          <linearGradient id="oc-cur-ll" x1="0.5" y1="0" x2="0.3" y2="1"><stop offset="0" stopColor="#2f2f2f" /><stop offset="1" stopColor="#9a9a9a" /></linearGradient>
          <linearGradient id="oc-cur-rl" x1="0.4" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#0c0d10" /><stop offset="1" stopColor="#2a2d33" /></linearGradient>
        </defs>
        {/* translucent back edges (revealed when the white facets fade) */}
        <g stroke="#c8c8c8" strokeWidth="0.25" opacity="0.45">
          <line x1="12" y1="12" x2="12" y2="2" />
          <line x1="12" y1="12" x2="3.2" y2="17" />
          <line x1="12" y1="12" x2="20.8" y2="17" />
        </g>
        {/* metallic faces */}
        <path d="M3.2 7 12 2 20.8 7Z" fill="url(#oc-cur-tb)" />
        <path d="M3.2 7 12 12 3.2 17Z" fill="url(#oc-cur-lu)" />
        <path d="M12 12 12 22 3.2 17Z" fill="url(#oc-cur-ll)" />
        <path d="M20.8 7 20.8 17 12 22Z" fill="url(#oc-cur-rl)" />
        {/* two white facets — pulse opacity to reveal the back edges */}
        <path className="pulse" d="M3.2 7 12 12 20.8 7Z" fill="#f2f2f2" />
        <path className="pulse" d="M12 12 20.8 7 12 22Z" fill="#f2f2f2" />
        {/* front edges */}
        <g stroke="#e8e8e8" strokeWidth="0.18" opacity="0.55">
          <path d="M12 2 20.8 7 20.8 17 12 22 3.2 17 3.2 7Z" />
          <line x1="12" y1="12" x2="3.2" y2="7" /><line x1="12" y1="12" x2="20.8" y2="7" /><line x1="12" y1="12" x2="12" y2="22" />
        </g>
      </svg>
    </span>
  );
}

// --- kimicode: eight-frame moon-phase cycle (Moonshot brand) -----------------
const KIMI_FRAMES = ['🌑', '🌒', '🌓', '🌔', '🌕', '🌖', '🌗', '🌘'];
function KimiCodeSpinner({ size }: { size: number }) {
  return (
    <span className="optio-sp-kimi" style={{ position: 'relative', display: 'inline-block', lineHeight: 1, width: size, height: size, fontSize: size }}>
      {KIMI_FRAMES.map((f, i) => (
        <span key={i} style={{ animationDelay: `${i * 120}ms` }} aria-hidden="true">{f}</span>
      ))}
    </span>
  );
}

// --- antigravity: braille "dots" spinner extracted from the agy CLI binary
// (the 10-frame ⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏ run — cli-spinners "dots", ~80ms/frame). Monochrome
// via currentColor. Ten stepped frames, same overlay technique as kimi's moons.
const AGY_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
function AntigravitySpinner({ size }: { size: number }) {
  return (
    <span className="optio-sp-agy" style={{ position: 'relative', display: 'inline-block', lineHeight: 1, width: size, height: size, fontSize: size }}>
      {AGY_FRAMES.map((f, i) => (
        <span key={i} style={{ animationDelay: `${i * 80}ms` }} aria-hidden="true">{f}</span>
      ))}
    </span>
  );
}

const BUILDERS: Record<SpinnerEngine, (p: { size: number }) => React.ReactElement> = {
  claudecode: ClaudeSpinner,
  opencode: OpencodeSpinner,
  grok: GrokSpinner,
  codex: CodexSpinner,
  cursor: CursorSpinner,
  kimicode: KimiCodeSpinner,
  antigravity: AntigravitySpinner,
};

/** On-brand native spinner for one engine. Renders nothing for an unknown
 *  engine (the caller falls back to the generic <Spin>). */
export function NativeSpinner({
  engine,
  size = 18,
  style,
}: {
  engine: string;
  size?: number;
  style?: CSSProperties;
}): React.ReactElement | null {
  ensureSpinnerStyle();
  const Builder = BUILDERS[engine as SpinnerEngine];
  if (!Builder) return null;
  return (
    <span role="status" aria-label="working" style={{ display: 'inline-flex', ...style }}>
      <Builder size={size} />
    </span>
  );
}
