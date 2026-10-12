import { describe, it, expect } from 'vitest';
import { isDarkColor } from '../themeMode.js';

describe('isDarkColor', () => {
  it.each([
    ['#ffffff', false], ['#fff', false], ['#f7faff', false], ['#ffffffcc', false],
    ['#141414', true], ['#000', true], ['#0d1826', true],
    ['rgb(247, 250, 255)', false], ['rgba(13, 24, 38, 0.9)', true], ['rgb(13 24 38)', true],
  ])('%s is dark: %s', (color, dark) => {
    expect(isDarkColor(color)).toBe(dark);
  });

  it('an unreadable colour counts as light', () => {
    expect(isDarkColor('transparent')).toBe(false);
  });
});
