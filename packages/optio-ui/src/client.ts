import { initTsrReactQuery } from '@ts-rest/react-query/v5';
import { initContract } from '@ts-rest/core';
import { processesContract, discoveryContract } from 'optio-contracts';

const c = initContract();
const apiContract = c.router(
  { processes: processesContract, discovery: discoveryContract },
  { pathPrefix: '/api' },
);

export type OptioClient = ReturnType<typeof createOptioClient>;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function createOptioClient(baseUrl: string): any {
  return initTsrReactQuery(apiContract, {
    baseUrl,
    baseHeaders: {},
  });
}
