// For description-only verbosity: pick a one-line summary from the tool input —
// its `description` when present, else the first non-empty string under a
// salient key, truncated. Empty string => show just the tool name.
const SALIENT_KEYS = ['description', 'command', 'file_path', 'path', 'pattern', 'query', 'url', 'prompt', 'title'];

export function toolSummary(input: unknown): string {
  if (input && typeof input === 'object' && !Array.isArray(input)) {
    const obj = input as Record<string, unknown>;
    for (const k of SALIENT_KEYS) {
      const v = obj[k];
      if (typeof v === 'string' && v.trim()) {
        const s = v.trim();
        return s.length > 120 ? s.slice(0, 117) + '…' : s;
      }
    }
  }
  return '';
}
