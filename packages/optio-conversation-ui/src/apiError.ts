// Translate raw engine/model-provider error strings into friendlier,
// actionable explanations for the conversation widget. Engine-neutral.
// Always falls back to the raw text — never hides information.

export function explainApiError(raw: string, status?: number | null): string {
  const r = (raw || '').toLowerCase();
  if (r.includes('content filter') || r.includes('content_filter')) {
    return (
      '⚠️ The response was blocked by the model provider’s safety filter — ' +
      'often caused by highly repetitive or flagged content earlier in the ' +
      'conversation. Try starting a fresh conversation.'
    );
  }
  if (status === 429 || r.includes('rate limit') || r.includes('rate_limit')) {
    return '⚠️ Rate-limited by the model provider — wait a moment and try again.';
  }
  if (status === 529 || r.includes('overloaded')) {
    return '⚠️ The model provider is overloaded — please retry shortly.';
  }
  if (status === 401 || status === 403 || r.includes('authentication') || r.includes('permission')) {
    return `⚠️ The model provider rejected the request (auth/permission). ${raw}`.trim();
  }
  return raw || 'The agent reported an error.';
}
