import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import { ConfigProvider, theme } from 'antd';
import { AnswerBlock } from '../AnswerBlock.js';
import { mermaidConfig, type MermaidThemeToken } from '../mermaidTheme.js';

// Mermaid diagrams take their colours from the host's antd theme (owner, 2026-10-27): X's
// Mermaid initializes mermaid with its light 'default' theme unless given a config, so a
// dark host showed dark text on a dark background.

// X's Mermaid, replaced by a stand-in that keeps the config it is handed: jsdom cannot lay
// out mermaid's SVG, and the config is what AnswerBlock contributes.
const seen = vi.hoisted(() => [] as unknown[]);
vi.mock('@ant-design/x', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@ant-design/x')>();
  return {
    ...actual,
    Mermaid: ({ children, config }: { children: string; config?: unknown }) => {
      seen.push(config);
      return <pre data-testid="mermaid">{children}</pre>;
    },
  };
});

const PALETTE = {
  blue6: '#3366ff', green6: '#22aa44', gold6: '#ddaa11', purple6: '#7744cc', cyan6: '#11aaaa',
  magenta6: '#cc3388', volcano6: '#dd5522', geekblue6: '#3355dd', lime6: '#99cc11', orange6: '#dd8811',
};
const DARK: MermaidThemeToken = {
  colorBgContainer: '#101820', colorText: 'rgba(255, 255, 255, 0.85)',
  colorTextSecondary: 'rgba(255, 255, 255, 0.65)', colorBorder: '#424242',
  colorPrimaryBg: '#111a2c', colorPrimaryBorder: '#15325b', colorTextLightSolid: '#fff', ...PALETTE,
};
const LIGHT: MermaidThemeToken = { ...DARK, colorBgContainer: '#ffffff', colorText: 'rgba(0, 0, 0, 0.88)' };

const vars = (config: unknown) => (config as { themeVariables: Record<string, unknown> }).themeVariables;

describe('mermaidConfig', () => {
  it("a dark host: mermaid's base theme in dark mode, the host's text blended onto its background", () => {
    const config = mermaidConfig(DARK);
    expect(config.theme).toBe('base');
    expect(vars(config)).toMatchObject({
      darkMode: true, background: '#101820', textColor: '#dbdcde', primaryTextColor: '#dbdcde',
      pieTitleTextColor: '#dbdcde', pieLegendTextColor: '#dbdcde', lineColor: '#abaeb1',
    });
  });

  it('a light host: light mode, its own text colour', () => {
    expect(vars(mermaidConfig(LIGHT))).toMatchObject({ darkMode: false, background: '#ffffff', textColor: '#1f1f1f' });
  });

  it("series take the host's preset palette, in pies and in xy charts", () => {
    const v = vars(mermaidConfig(DARK));
    expect(v).toMatchObject({ pie1: '#3366ff', pie2: '#22aa44', pie10: '#dd8811', pieSectionTextColor: '#ffffff' });
    expect((v.xyChart as { plotColorPalette: string }).plotColorPalette).toBe(
      '#3366ff,#22aa44,#ddaa11,#7744cc,#11aaaa,#cc3388,#dd5522,#3355dd,#99cc11,#dd8811');
  });
});

describe('AnswerBlock: a mermaid fence', () => {
  beforeEach(() => { seen.length = 0; });
  const PIE = '```mermaid\npie title Share\n  "A" : 3\n  "B" : 1\n```';

  it('under a dark antd theme gets the dark config from that theme', () => {
    render(
      <ConfigProvider theme={{ algorithm: theme.darkAlgorithm, token: { colorBgContainer: '#101820', colorText: 'rgba(255, 255, 255, 0.85)' } }}>
        <AnswerBlock text={PIE} />
      </ConfigProvider>,
    );
    expect(seen.length).toBeGreaterThan(0);
    expect(vars(seen.at(-1))).toMatchObject({ darkMode: true, background: '#101820', textColor: '#dbdcde' });
  });

  it("under antd's default light theme gets the light config", () => {
    render(<AnswerBlock text={PIE} />);
    expect(vars(seen.at(-1))).toMatchObject({ darkMode: false, background: '#ffffff', textColor: '#1f1f1f' });
  });

  it('keeps the same config object while the theme stays, so mermaid is not set up again', () => {
    const { rerender } = render(<AnswerBlock text={PIE} />);
    rerender(<AnswerBlock text={PIE + '\n\nmore'} />);
    expect(seen.length).toBeGreaterThan(1);
    expect(seen[0]).toEqual(expect.objectContaining({ theme: 'base' }));
    expect(new Set(seen).size).toBe(1);
  });
});
