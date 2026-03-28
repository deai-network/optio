#!/usr/bin/env node
import { startServer } from './server.js';

const config = {
  mongodbUrl: process.env.MONGODB_URL || 'mongodb://localhost:27017/optio',
  redisUrl: process.env.REDIS_URL || 'redis://localhost:6379',
  port: parseInt(process.env.PORT || '3000', 10),
};

startServer(config).catch((err) => {
  console.error('Failed to start Optio Dashboard:', err);
  process.exit(1);
});
