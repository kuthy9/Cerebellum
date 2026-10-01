import { describe, expect, it } from "vitest";
import { layoutWaterfall, type Span } from "./waterfall";

const spans: Span[] = [
  { span_id: "a#1", parent_id: null, step_id: "a", kind: "step", label: "a #1", start: 10, end: 12, status: "succeeded" },
  { span_id: "b#1", parent_id: null, step_id: "b", kind: "step", label: "b #1", start: 12, end: null, status: "waiting" },
  { span_id: "a#1:connector:x", parent_id: "a#1", step_id: "a", kind: "connector", label: "sql query", start: 11, end: 11.5, status: "succeeded" },
];

describe("layoutWaterfall", () => {
  it("nests calls under their step attempt in start order", () => {
    const rows = layoutWaterfall(spans, 20);
    expect(rows.map((r) => [r.span.span_id, r.depth])).toEqual([
      ["a#1", 0],
      ["a#1:connector:x", 1],
      ["b#1", 0],
    ]);
  });

  it("positions bars as percentages and extends open spans to now", () => {
    const [a, call, b] = layoutWaterfall(spans, 20);
    expect(a.offset).toBe(0);
    expect(a.width).toBeCloseTo(20);
    expect(call.offset).toBeCloseTo(10);
    expect(call.width).toBeCloseTo(5);
    expect(b.offset).toBeCloseTo(20);
    expect(b.width).toBeCloseTo(80);
    expect(b.duration).toBe(8);
  });

  it("keeps zero-length spans visible and handles no spans", () => {
    const [row] = layoutWaterfall([{ ...spans[0], start: 5, end: 5 }], 5);
    expect(row.width).toBeGreaterThan(0);
    expect(layoutWaterfall([], 1)).toEqual([]);
  });
});
