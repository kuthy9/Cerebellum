export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "";
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(2)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

export function fmtCost(usd: number | null | undefined): string {
  return usd ? `$${usd.toFixed(4)}` : "";
}

export function fmtAge(ts: number, now: number): string {
  const delta = Math.max(now - ts, 0);
  if (delta < 60) return `${Math.floor(delta)}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

export function fmtPercent(ratio: number | null | undefined): string {
  return ratio == null ? "—" : `${Math.round(ratio * 100)}%`;
}

export function fmtAmount(value: unknown): string {
  return typeof value === "number" ? value.toLocaleString("en-US", { maximumFractionDigits: 2 }) : String(value);
}

export function fmtJson(value: unknown): string {
  return value === undefined ? "" : JSON.stringify(value, null, 2);
}

/** Pretty-print text that holds JSON (e.g. an LLM response); other text is returned as is. */
export function prettyJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}
