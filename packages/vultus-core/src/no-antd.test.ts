import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { test, expect } from 'vitest';

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    if (e.isDirectory()) return walk(p);
    return /\.(ts|tsx)$/.test(e.name) ? [p] : [];
  });
}

test('vultus-core never imports antd (antd-free by design — see AGENTS.md)', () => {
  // vitest runs with cwd = package root.
  const offenders = walk(join(process.cwd(), 'src'))
    .filter((f) => !/no-antd\.test\.ts$/.test(f))
    .filter((f) => /from\s+['"]antd['"]|from\s+['"]antd\//.test(readFileSync(f, 'utf8')));
  expect(offenders).toEqual([]);
});
