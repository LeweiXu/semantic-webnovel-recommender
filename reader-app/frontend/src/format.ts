// Shared display formatting. Counts come from the backend already in the right
// unit for the language (characters for Chinese, whitespace words for English),
// so all this does is keep them short and readable.

export function formatCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  return n.toLocaleString();
}

export function formatWords(n: number): string {
  return `${formatCount(n)} words`;
}
