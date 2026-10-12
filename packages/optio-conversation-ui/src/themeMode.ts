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
  const rgb = rgbOf(color.trim());
  if (!rgb) return false;
  const [r, g, b] = rgb;
  return (r * 299 + g * 587 + b * 114) / 1000 < 128;
}

function rgbOf(color: string): [number, number, number] | null {
  const hex = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec(color);
  if (hex) {
    const h = hex[1].length <= 4 ? [...hex[1]].map((c) => c + c).join('') : hex[1];
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }
  const fn = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i.exec(color);
  return fn ? [Number(fn[1]), Number(fn[2]), Number(fn[3])] : null;
}
