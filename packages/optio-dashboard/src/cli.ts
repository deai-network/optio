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

const config = {
  mongodbUrl: process.env.MONGODB_URL || 'mongodb://localhost:27017/optio',
  redisUrl: process.env.REDIS_URL || 'redis://localhost:6379',
  port: parseInt(process.env.PORT || '3000', 10),
  password,
};

startServer(config).catch((err) => {
  console.error('Failed to start Optio Dashboard:', err);
  process.exit(1);
});
