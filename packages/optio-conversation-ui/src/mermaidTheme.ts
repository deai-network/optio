import { useMemo } from 'react';
import { theme, type GlobalToken } from 'antd';
import type { MermaidProps } from '@ant-design/x';
import { hexOn, isDarkColor } from './themeMode.js';

// Mermaid diagrams in the host's antd theme (owner, 2026-10-27). X's Mermaid initializes
// mermaid with its light 'default' theme unless it is given a config, so on a dark host a
// diagram kept dark text and pastel fills on the dark answer. mermaid's 'base' theme is the
// one made to be coloured through themeVariables; its theming reads hex colours only, so each
// token is laid onto the host's background first (antd's text colours are translucent).

export type MermaidConfig = NonNullable<MermaidProps['config']>;

// The series of pies and xy charts: antd's preset colours, sixth shade, as the host theme's
// algorithm made them (the dark algorithm's darker shades included) and with its overrides.
const SERIES = [
  'blue6', 'green6', 'gold6', 'purple6', 'cyan6', 'magenta6', 'volcano6', 'geekblue6', 'lime6', 'orange6',
] as const;

export type MermaidThemeToken = Pick<
  GlobalToken,
  | 'colorBgContainer' | 'colorText' | 'colorTextSecondary' | 'colorBorder'
  | 'colorPrimaryBg' | 'colorPrimaryBorder' | 'colorTextLightSolid' | (typeof SERIES)[number]
>;

/** mermaid's config for a host theme: the 'base' theme, coloured from the antd token. */
export function mermaidConfig(token: MermaidThemeToken): MermaidConfig {
  const background = hexOn(token.colorBgContainer, '#ffffff') ?? '#ffffff';
  const on = (color: string) => hexOn(color, background) ?? undefined;
  const text = on(token.colorText);
  const muted = on(token.colorTextSecondary);
  const series = SERIES.map((key) => on(token[key])).filter((color): color is string => color !== undefined);
  const themeVariables = defined({
    darkMode: isDarkColor(background),
    background,
    textColor: text,
    primaryTextColor: text,
    primaryColor: on(token.colorPrimaryBg),
    primaryBorderColor: on(token.colorPrimaryBorder),
    lineColor: muted,
    pieTitleTextColor: text,
    pieLegendTextColor: text,
    pieSectionTextColor: on(token.colorTextLightSolid),
    pieStrokeColor: background,
    pieOuterStrokeColor: on(token.colorBorder),
    pieOpacity: '1',
    ...Object.fromEntries(series.map((color, i) => [`pie${i + 1}`, color])),
    xyChart: defined({
      backgroundColor: background,
      titleColor: text,
      xAxisLabelColor: text,
      xAxisTitleColor: text,
      xAxisTickColor: muted,
      xAxisLineColor: muted,
      yAxisLabelColor: text,
      yAxisTitleColor: text,
      yAxisTickColor: muted,
      yAxisLineColor: muted,
      plotColorPalette: series.join(','),
    }),
  });
  return { theme: 'base', themeVariables };
}

/** The host theme's mermaid config: one object per theme, because X's Mermaid sets mermaid up
 *  again and redraws whenever the config object changes. */
export function useMermaidConfig(): MermaidConfig {
  const { token } = theme.useToken();
  return useMemo(() => mermaidConfig(token), [token]);
}

// mermaid copies every themeVariables key over its defaults, an undefined one included.
function defined<T extends Record<string, unknown>>(values: T): Partial<T> {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined)) as Partial<T>;
}
