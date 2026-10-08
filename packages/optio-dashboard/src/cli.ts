#!/usr/bin/env node
import { startServer } from './server.js';

const password = process.env.OPTIO_PASSWORD;
if (!password) {
  console.error(
    'Error: OPTIO_PASSWORD environment variable is required.\n' +
    'Set it to the password that will be required to access the dashboard.\n' +
    'Example: OPTIO_PASSWORD=mysecret optio-dashboard'
  );
  process.exit(1);
}

const verbose =
  process.argv.includes('--verbose') ||
  process.argv.includes('-v') ||
  process.env.OPTIO_VERBOSE === '1' ||
  process.env.OPTIO_VERBOSE === 'true';

// MONGODB_URL, when set and naming a database (its path), pins the dashboard
// to that one database; unset, it discovers optio instances in every database
// on the server. OPTIO_PREFIX pins it to one prefix. See README.
const mongodbUrlEnv = process.env.MONGODB_URL;
const config = {
  mongodbUrl: mongodbUrlEnv || 'mongodb://localhost:27017/optio',
  pinDatabase: Boolean(mongodbUrlEnv && new URL(mongodbUrlEnv).pathname.slice(1)),
  prefix: process.env.OPTIO_PREFIX || undefined,
  redisUrl: process.env.REDIS_URL || 'redis://localhost:6379',
  port: parseInt(process.env.PORT || '3000', 10),
  password,
  verbose,
};

startServer(config).catch((err) => {
  console.error('Failed to start Optio Dashboard:', err);
  process.exit(1);
});
