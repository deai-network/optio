import type { Decision } from './types.js';

export const allow = (reason?: string): Decision => ({ verdict: true, reason });
export const deny = (reason?: string): Decision => ({ verdict: false, reason });
export const denyWithReason = (reason: string): Decision => ({ verdict: false, reason });

export const getVerdict = (d: Decision | undefined, fallback: boolean): boolean =>
  d === undefined ? fallback : typeof d === 'boolean' ? d : d.verdict;

export const getReason = (d: Decision | undefined): string | undefined =>
  d === undefined ? undefined : typeof d === 'boolean' ? undefined : d.reason;

export const andDecisions = (a: Decision, b: Decision): Decision => {
  const av = getVerdict(a, false);
  const bv = getVerdict(b, false);
  if (av && bv) {
    const reasons = [getReason(a), getReason(b)].filter(Boolean) as string[];
    return { verdict: true, reason: reasons.join('; ') || undefined };
  }
  return av ? b : a;
};
