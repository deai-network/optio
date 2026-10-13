import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

// Enforces the spec's "One choke point": every process read whose result
// can reach a response goes through `processReads` (process-reads.ts). No
// other module may name the `{prefix}_processes` collection, call
// `processCollection(` outside its allowlist, or reach `.unfiltered`
// outside access-scope.ts / process-reads.ts. A new read path therefore
// either goes through `processReads` or fails this test until it is
// deliberately allowlisted, with a reason, in review.

const SRC_DIR = join(dirname(fileURLToPath(import.meta.url)), '..');

// Migrated by Task 4 / Task 5: these still name the collection directly and
// are exempt from the three rules below until that work lands.
const PENDING = [
  'stream-poller.ts',
  'adapters/fastify.ts',
  'adapters/express.ts',
  'adapters/nextjs-app.ts',
  'adapters/nextjs-pages.ts',
];

// `processCollection(` is called only here, outside process-reads.ts itself.
const PROCESS_COLLECTION_ALLOWLIST: Record<string, string> = {
  'process-change-hub.ts': 'a change stream, which serves no documents',
  'discovery.ts': "instance discovery, gated by 'instances'",
  'widget-proxy-core.ts': 'an upstream lookup that runs only after gateProcess admitted the process',
  'agent-input.ts': 'an upstream lookup that runs only after gateProcess admitted the process',
};

function listTsFiles(dir: string, base: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (entry === '__tests__') continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...listTsFiles(full, base));
    } else if (entry.endsWith('.ts')) {
      out.push(relative(base, full).split('\\').join('/'));
    }
  }
  return out;
}

// process-reads.ts is the choke point itself: it is exempt from its own
// rules by definition (it is where the collection is named, where
// `processCollection(` is implemented and called, and where `.unfiltered`
// is produced).
const ALL_FILES = listTsFiles(SRC_DIR, SRC_DIR).filter((f) => f !== 'process-reads.ts');
const CHECKED_FILES = ALL_FILES.filter((f) => !PENDING.includes(f));

function read(file: string): string {
  return readFileSync(join(SRC_DIR, file), 'utf8');
}

describe('process-reads guard', () => {
  it('names no processes collection outside process-reads.ts', () => {
    const offenders = CHECKED_FILES.filter((f) => /\$\{[^}]*\}_processes/.test(read(f)));
    expect(offenders).toEqual([]);
  });

  it('calls processCollection( only from its allowlist, each with a reason', () => {
    const offenders = CHECKED_FILES.filter(
      (f) => read(f).includes('processCollection(') && !(f in PROCESS_COLLECTION_ALLOWLIST),
    );
    expect(offenders).toEqual([]);
  });

  it('uses .unfiltered only in access-scope.ts and process-reads.ts', () => {
    const offenders = CHECKED_FILES.filter((f) => f !== 'access-scope.ts' && read(f).includes('.unfiltered'));
    expect(offenders).toEqual([]);
  });
});
