import { initQueryClient } from '@ts-rest/react-query';
import { initContract } from '@ts-rest/core';
import { processesContract } from 'feldwebel-contracts';

const c = initContract();
const apiContract = c.router({ processes: processesContract }, { pathPrefix: '/api' });

export type FeldwebelClient = ReturnType<typeof createFeldwebelClient>;

export function createFeldwebelClient(baseUrl: string) {
  return initQueryClient(apiContract, {
    baseUrl,
    baseHeaders: {},
  });
}
