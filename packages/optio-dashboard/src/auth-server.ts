// packages/optio-dashboard/src/auth-server.ts
import { betterAuth } from 'better-auth';
import { mongodbAdapter } from 'better-auth/adapters/mongodb';
import type { Db } from 'mongodb';
import bcrypt from 'bcryptjs';

export function createAuth(db: Db, secret: string) {
  return betterAuth({
    database: mongodbAdapter(db),
    emailAndPassword: { enabled: true },
    secret,
  });
}

export type Auth = ReturnType<typeof createAuth>;

export async function upsertAdminUser(db: Db, auth: Auth, password: string): Promise<void> {
  const existing = await db.collection('user').findOne({ email: 'admin@localhost' });

  if (!existing) {
    await auth.api.signUpEmail({
      body: { email: 'admin@localhost', password, name: 'Admin' },
    });
    return;
  }

  // User exists — update the stored password hash so it matches the current env var.
  // Better Auth stores credential passwords in the 'account' collection with
  // providerId = 'credential'. We update it directly using bcrypt (same algorithm
  // Better Auth uses internally).
  const hashed = await bcrypt.hash(password, 10);
  const userId = (existing.id as string) ?? (existing._id as any).toString();
  await db.collection('account').updateOne(
    { userId, providerId: 'credential' },
    { $set: { password: hashed } }
  );
}
