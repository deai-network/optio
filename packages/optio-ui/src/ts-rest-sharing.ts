import { replaceEqualDeep } from '@tanstack/react-query';

interface TsRestResponse {
  status: number;
  body: unknown;
}

function isTsRestResponse(data: unknown): data is TsRestResponse {
  return typeof data === 'object' && data !== null && typeof (data as { status?: unknown }).status === 'number' && 'body' in data;
}

/**
 * A react-query `structuralSharing` for ts-rest responses (`{ status, body, headers }`).
 *
 * ts-rest hands back the fetch's own `Headers` object, which react-query's default structural
 * sharing does not look into (it is no plain object), so every fetch makes a new `data` even when
 * nothing changed, and whatever reads it re-renders. This compares what the response says: the
 * same status and a deep-equal body keep the previous response object; otherwise it is the new
 * response, its body sharing every part that stayed the same with the previous one
 * (`replaceEqualDeep`). Data that is not a ts-rest response is shared as react-query does by
 * default.
 *
 * optio-ui's polling and list queries use it; a host can give it to its own ts-rest queries, or
 * to all of them as a `QueryClient` default (`defaultOptions.queries.structuralSharing`).
 */
export function shareTsRestResponse(oldData: unknown, newData: unknown): unknown {
  if (!isTsRestResponse(oldData) || !isTsRestResponse(newData)) return replaceEqualDeep(oldData, newData);
  const body = replaceEqualDeep(oldData.body, newData.body);
  if (oldData.status === newData.status && body === oldData.body) return oldData;
  return body === newData.body ? newData : { ...newData, body };
}
