import type { Db } from 'mongodb';

const REQUIRED_FIELDS = ['processId', 'rootId', 'depth'];

export async function discoverPrefixes(db: Db): Promise<string[]> {
  const collections = await db.listCollections().toArray();
  const candidates = collections
    .map((c) => c.name)
    .filter((name) => name.endsWith('_processes'))
    .map((name) => name.slice(0, -'_processes'.length));

  const confirmed: string[] = [];

  for (const prefix of candidates) {
    const doc = await db.collection(`${prefix}_processes`).findOne();
    if (doc && REQUIRED_FIELDS.every((f) => f in doc)) {
      confirmed.push(prefix);
    }
  }

  return confirmed.sort();
}
