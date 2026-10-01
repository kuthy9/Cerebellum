export interface Span {
  span_id: string;
  parent_id: string | null;
  step_id: string | null;
  kind: string;
  label: string;
  start: number;
  end: number | null;
  status: string;
}

export interface WaterfallRow {
  span: Span;
  depth: number;
  offset: number; // % of the run's time range
  width: number; // % of the run's time range
  duration: number; // seconds
}

/** Waterfall rows: each step attempt followed by its LLM / connector calls, bars as percentages of
 * the run's time range. Open spans (running, waiting) extend to `now`. */
export function layoutWaterfall(spans: Span[], now: number): WaterfallRow[] {
  if (!spans.length) return [];
  const t0 = Math.min(...spans.map((span) => span.start));
  const t1 = Math.max(...spans.map((span) => span.end ?? now));
  const total = Math.max(t1 - t0, 1e-9);
  const ids = new Set(spans.map((span) => span.span_id));
  const children = new Map<string, Span[]>();
  const roots: Span[] = [];
  for (const span of spans) {
    if (span.parent_id && ids.has(span.parent_id)) {
      children.set(span.parent_id, [...(children.get(span.parent_id) ?? []), span]);
    } else {
      roots.push(span);
    }
  }
  const byStart = (a: Span, b: Span) => a.start - b.start;
  const rows: WaterfallRow[] = [];
  const add = (span: Span, depth: number) => {
    const end = span.end ?? now;
    const offset = Math.min(((span.start - t0) / total) * 100, 99.5);
    const width = Math.min(Math.max(((end - span.start) / total) * 100, 0.5), 100 - offset);
    rows.push({ span, depth, offset, width, duration: end - span.start });
    for (const child of [...(children.get(span.span_id) ?? [])].sort(byStart)) add(child, depth + 1);
  };
  for (const root of [...roots].sort(byStart)) add(root, 0);
  return rows;
}
