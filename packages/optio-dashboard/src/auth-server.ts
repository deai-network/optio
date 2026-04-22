// packages/optio-dashboard/src/auth-server.ts
import { betterAuth } from 'better-auth';
import { mongodbAdapter } from 'better-auth/adapters/mongodb';
import type { Db } from 'mongodb';
import { createHash } from 'crypto';

const ADMIN_EMAIL = 'admin@optio.local';

export function createAuth(db: Db, password: string, baseURL: string) {
  const secret = createHash('sha256')
    .update('optio-dashboard-auth:' + password)
    .digest('base64');
  return betterAuth({
    database: mongodbAdapter(db),
    emailAndPassword: {
      enabled: true,
      minPasswordLength: 1,
      disableSignUp: true,
    },
    secret,
    baseURL,
    trustedOrigins: ['http://localhost:5173'],
  });
}

export type Auth = ReturnType<typeof createAuth>;

export async function upsertAdminUser(auth: Auth, password: string): Promise<void> {
  const ctx = await auth.$context;
  const hashed = await ctx.password.hash(password);

  const existing = await ctx.internalAdapter.findUserByEmail(ADMIN_EMAIL, {
    includeAccounts: true,
  });

  if (!existing) {
    const user = await ctx.internalAdapter.createUser({
      email: ADMIN_EMAIL,
      name: 'Admin',
      emailVerified: true,
    });
    await ctx.internalAdapter.linkAccount({
      userId: user.id,
      providerId: 'credential',
      accountId: user.id,
      password: hashed,
    });
    return;
  }

  await ctx.internalAdapter.updatePassword(existing.user.id, hashed);
}
