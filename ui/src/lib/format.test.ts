import { describe, expect, it } from "vitest";
import { fmtAge, fmtAmount, fmtCost, fmtDuration, fmtJson, fmtPercent, prettyJson } from "./format";

describe("format", () => {
  it("formats durations like the CLI", () => {
    expect(fmtDuration(null)).toBe("");
    expect(fmtDuration(0.0424)).toBe("42ms");
    expect(fmtDuration(15.623)).toBe("15.62s");
    expect(fmtDuration(90)).toBe("1.5m");
    expect(fmtDuration(5400)).toBe("1.5h");
  });

  it("hides zero cost and formats real cost", () => {
    expect(fmtCost(0)).toBe("");
    expect(fmtCost(0.01234)).toBe("$0.0123");
  });

  it("formats ages, percentages and amounts", () => {
    expect(fmtAge(100, 159)).toBe("59s ago");
    expect(fmtAge(0, 7200)).toBe("2h ago");
    expect(fmtPercent(null)).toBe("—");
    expect(fmtPercent(0.875)).toBe("88%");
    expect(fmtAmount(1234.5)).toBe("1,234.5");
    expect(fmtAmount("n/a")).toBe("n/a");
  });

  it("pretty-prints JSON values and JSON text", () => {
    expect(fmtJson({ a: 1 })).toBe('{\n  "a": 1\n}');
    expect(fmtJson(undefined)).toBe("");
    expect(prettyJson('{"ok":true}')).toBe('{\n  "ok": true\n}');
    expect(prettyJson("not json")).toBe("not json");
  });
});
