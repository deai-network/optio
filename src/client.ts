import { initQueryClient } from '@ts-rest/react-query';
import { initContract } from '@ts-rest/core';
import { processesContract } from 'optio-contracts';

const c = initContract();
const apiContract = c.router({ processes: processesContract }, { pathPrefix: '/api' });

export type OptioClient = ReturnType<typeof createOptioClient>;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function createOptioClient(baseUrl: string): any {
  return initQueryClient(apiContract, {
    baseUrl,
    baseHeaders: {},
  });
}
