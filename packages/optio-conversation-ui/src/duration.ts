// Compact elapsed time for tool rows: "42s", "3m 04s", "1h 07m".
export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  if (total < 60) return `${total}s`;
  const seconds = total % 60;
  const hours = Math.floor(total / 3600);
  if (hours === 0) return `${Math.floor(total / 60)}m ${String(seconds).padStart(2, '0')}s`;
  const minutes = Math.floor(total / 60) % 60;
  return `${hours}h ${String(minutes).padStart(2, '0')}m`;
}
