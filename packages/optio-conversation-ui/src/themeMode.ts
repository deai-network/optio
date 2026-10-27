import { theme } from 'antd';

// Light or dark, for the Ant Design X widgets that come in both (glass bubbles,
// XMarkdown's theme, code highlighting): read from the background the view
// sits on (colorBgContainer), not from the theme's identity. A host theme with
// an algorithm of its own (daeo-style wraps antd's light and dark ones) is
// never antd's default theme, so `theme.id === 0` called its light look dark.
export function useDarkTheme(): boolean {
  const { token } = theme.useToken();
  return isDarkColor(token.colorBgContainer);
}

/** Perceived brightness below the middle (the formula of antd's
 *  @ant-design/fast-color `isDark`). Reads #rgb(a), #rrggbb(aa), rgb() and
 *  rgba(); anything else counts as light. */
export function isDarkColor(color: string): boolean {
  const rgba = rgbaOf(color.trim());
  if (!rgba) return false;
  const [r, g, b] = rgba;
  return (r * 299 + g * 587 + b * 114) / 1000 < 128;
}

/** `color` laid onto the opaque `background` (its alpha blended in), as `#rrggbb`: the form
 *  mermaid's theming reads (hex only). Null when either colour is unreadable. */
export function hexOn(color: string, background: string): string | null {
  const c = rgbaOf(color.trim());
  const b = rgbaOf(background.trim());
  if (!c || !b) return null;
  const channel = (i: number) => Math.round(c[i] * c[3] + b[i] * (1 - c[3]));
  return '#' + [0, 1, 2].map((i) => channel(i).toString(16).padStart(2, '0')).join('');
}

function rgbaOf(color: string): [number, number, number, number] | null {
  const hex = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec(color);
  if (hex) {
    const h = hex[1].length <= 4 ? [...hex[1]].map((c) => c + c).join('') : hex[1];
    const alpha = h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1;
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16), alpha];
  }
  const fn = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:[\s,/]+([\d.]+)(%?))?/i.exec(color);
  if (!fn) return null;
  const alpha = fn[4] === undefined ? 1 : Number(fn[4]) / (fn[5] ? 100 : 1);
  return [Number(fn[1]), Number(fn[2]), Number(fn[3]), alpha];
}
