// Hand-built fixture: realistic 6-process tree, ~40 interleaved log entries.
// Mirrors what useProcessStream would deliver after backend SSE updates.

export interface LogEntry {
  timestamp: string;
  level: string;
  message: string;
  data?: Record<string, unknown>;
  processId: string;
  processLabel: string;
  // legacy alias still consumed by ProcessLogPanel today
  processName?: string;
}

export interface ProcessNode {
  _id: string;
  parentId: string | null;
  name: string;
  description?: string | null;
  status: { state: string; error?: string; runningSince?: string };
  progress: { percent: number | null; message?: string };
  cancellable: boolean;
  depth: number;
  order: number;
  children: ProcessNode[];
}

const T0 = Date.parse('2026-05-14T10:00:00.000Z');
const t = (ms: number) => new Date(T0 + ms).toISOString();

// id -> label (process names)
const PROCS: Record<string, { label: string; parent: string | null; depth: number }> = {
  root: { label: 'ingest-batch-2026-05-14', parent: null, depth: 0 },
  fetch: { label: 'fetch-sources', parent: 'root', depth: 1 },
  parse: { label: 'parse-documents', parent: 'root', depth: 1 },
  normalize: { label: 'normalize', parent: 'parse', depth: 2 },
  validate: { label: 'validate-schema', parent: 'parse', depth: 2 },
  upload: { label: 'upload-results', parent: 'root', depth: 1 },
};

export const tree: ProcessNode = {
  _id: 'root',
  parentId: null,
  name: PROCS.root.label,
  status: { state: 'running', runningSince: t(0) },
  progress: { percent: 42, message: 'parsing documents' },
  cancellable: true,
  depth: 0,
  order: 0,
  children: [
    {
      _id: 'fetch',
      parentId: 'root',
      name: PROCS.fetch.label,
      status: { state: 'done' },
      progress: { percent: 100 },
      cancellable: false,
      depth: 1,
      order: 0,
      children: [],
    },
    {
      _id: 'parse',
      parentId: 'root',
      name: PROCS.parse.label,
      status: { state: 'running', runningSince: t(3000) },
      progress: { percent: 60, message: '60/100 documents' },
      cancellable: true,
      depth: 1,
      order: 1,
      children: [
        {
          _id: 'normalize',
          parentId: 'parse',
          name: PROCS.normalize.label,
          status: { state: 'running', runningSince: t(4000) },
          progress: { percent: null },
          cancellable: true,
          depth: 2,
          order: 0,
          children: [],
        },
        {
          _id: 'validate',
          parentId: 'parse',
          name: PROCS.validate.label,
          status: { state: 'running', runningSince: t(4500) },
          progress: { percent: 80 },
          cancellable: true,
          depth: 2,
          order: 1,
          children: [],
        },
      ],
    },
    {
      _id: 'upload',
      parentId: 'root',
      name: PROCS.upload.label,
      status: { state: 'scheduled' },
      progress: { percent: null },
      cancellable: false,
      depth: 1,
      order: 2,
      children: [],
    },
  ],
};

function entry(ms: number, pid: keyof typeof PROCS, level: string, message: string, data?: Record<string, unknown>): LogEntry {
  return {
    timestamp: t(ms),
    level,
    message,
    data,
    processId: pid,
    processLabel: PROCS[pid].label,
    processName: PROCS[pid].label,
  };
}

// Per-process derived info: depth, ancestor labels (root→…→self), stable color.
export interface ProcInfo {
  id: string;
  label: string;
  depth: number;
  ancestors: string[]; // labels from root → self (inclusive)
  color: string;       // hsl, stable per id
}

// Perceptually-distinct palette. Stride through it so adjacent assignment
// indices land far apart in hue, preventing siblings (which often interleave
// in the log stream) from getting near-identical colors.
const PALETTE = [
  '#ef4444', // red
  '#10b981', // emerald
  '#3b82f6', // blue
  '#f59e0b', // amber
  '#8b5cf6', // violet
  '#06b6d4', // cyan
  '#ec4899', // pink
  '#84cc16', // lime
  '#f97316', // orange
  '#a855f7', // purple
];
const STRIDE = 3; // gcd(10,3)=1 → covers full palette before repeating

// DFS over the tree to assign sequential indices. Stable per tree shape.
function dfsOrder(): string[] {
  const childrenOf: Record<string, string[]> = {};
  for (const [id, p] of Object.entries(PROCS)) {
    if (p.parent) (childrenOf[p.parent] ??= []).push(id);
  }
  const out: string[] = [];
  const visit = (id: string) => {
    out.push(id);
    for (const c of childrenOf[id] ?? []) visit(c);
  };
  const roots = Object.keys(PROCS).filter((id) => PROCS[id].parent === null);
  for (const r of roots) visit(r);
  return out;
}

function colorFor(orderIdx: number): string {
  return PALETTE[(orderIdx * STRIDE) % PALETTE.length];
}

export const processIndex: Record<string, ProcInfo> = (() => {
  const order = dfsOrder();
  const out: Record<string, ProcInfo> = {};
  order.forEach((id, idx) => {
    const ancestors: string[] = [];
    let cur: string | null = id;
    while (cur) {
      ancestors.unshift(PROCS[cur].label);
      cur = PROCS[cur].parent;
    }
    out[id] = { id, label: PROCS[id].label, depth: PROCS[id].depth, ancestors, color: colorFor(idx) };
  });
  return out;
})();

// 40 interleaved entries — varied depths, levels, timestamps close together
export const logs: LogEntry[] = [
  entry(0, 'root', 'event', 'process started'),
  entry(120, 'root', 'info', 'loaded configuration', { configFile: '/etc/ingest.yml' }),
  entry(300, 'fetch', 'event', 'process started'),
  entry(450, 'fetch', 'info', 'connecting to source A'),
  entry(800, 'fetch', 'debug', 'http GET https://src.example/api/list -> 200'),
  entry(1100, 'fetch', 'info', 'fetched 120 records from source A'),
  entry(1400, 'fetch', 'warning', 'source B returned partial result; retrying'),
  entry(1800, 'fetch', 'info', 'fetched 80 records from source B'),
  entry(2200, 'fetch', 'event', 'process finished'),
  entry(2500, 'root', 'info', 'fetch phase complete; 200 total records'),
  entry(3000, 'parse', 'event', 'process started'),
  entry(3100, 'parse', 'info', 'beginning parse pipeline'),
  entry(3400, 'parse', 'debug', 'spawning normalize + validate sub-tasks'),
  entry(4000, 'normalize', 'event', 'process started'),
  entry(4100, 'normalize', 'info', 'loaded 200 raw records'),
  entry(4500, 'validate', 'event', 'process started'),
  entry(4600, 'validate', 'info', 'schema version 3 loaded'),
  entry(4900, 'normalize', 'debug', 'applying field aliases'),
  entry(5100, 'validate', 'debug', 'record 1/200 ok'),
  entry(5200, 'normalize', 'warning', 'record 17 missing field `country`, defaulting to "unknown"'),
  entry(5400, 'validate', 'error', 'record 23 failed schema: `price` must be number, got string'),
  entry(5500, 'parse', 'warning', 'validation reported 1 schema failure'),
  entry(5800, 'normalize', 'debug', 'applying unit conversions'),
  entry(6000, 'validate', 'debug', 'record 100/200 ok'),
  entry(6300, 'normalize', 'info', 'normalized 200/200 records'),
  entry(6400, 'normalize', 'event', 'process finished'),
  entry(6700, 'validate', 'debug', 'record 200/200 ok'),
  entry(6900, 'validate', 'info', 'validation complete: 199 ok, 1 error'),
  entry(7000, 'validate', 'event', 'process finished'),
  entry(7100, 'parse', 'info', 'sub-tasks complete; merging results'),
  entry(7400, 'parse', 'debug', 'writing intermediate to /tmp/ingest/parse-out.jsonl'),
  entry(7700, 'parse', 'event', 'process finished'),
  entry(7900, 'root', 'info', 'parse phase complete'),
  entry(8000, 'upload', 'event', 'process started'),
  entry(8100, 'upload', 'info', 'opening connection to warehouse'),
  entry(8400, 'upload', 'debug', 'authenticated as ingest-bot'),
  entry(8700, 'upload', 'info', 'streaming 199 records'),
  entry(9200, 'upload', 'warning', 'warehouse acks slow (>500ms)'),
  entry(9600, 'upload', 'info', 'upload complete: 199 records committed'),
  entry(9700, 'upload', 'event', 'process finished'),
];
