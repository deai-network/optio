import { describe, it, expect } from 'vitest';
import { hexOn, isDarkColor } from '../themeMode.js';

describe('hexOn', () => {
  it.each([
    ['#3366FF', '#000000', '#3366ff'],
    ['#fff', '#000000', '#ffffff'],
    ['rgb(13 24 38)', '#ffffff', '#0d1826'],
    ['rgba(255, 255, 255, 0.85)', '#101820', '#dbdcde'],
    ['rgba(0, 0, 0, 0.88)', '#ffffff', '#1f1f1f'],
    ['#ffffff80', '#000000', '#808080'],
  ])('%s on %s is %s', (color, background, hex) => {
    expect(hexOn(color, background)).toBe(hex);
  });

  it('an unreadable colour has no hex', () => {
    expect(hexOn('transparent', '#000000')).toBeNull();
  });
});

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
