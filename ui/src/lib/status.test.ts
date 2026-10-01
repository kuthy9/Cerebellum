import { describe, expect, it } from "vitest";
import { SPAN_TONE } from "./status";

describe("SPAN_TONE", () => {
  it("does not draw spans closed by a reset or a cancel in the running colour", () => {
    for (const status of ["interrupted", "cancelled"]) {
      expect(SPAN_TONE[status] ?? "accent").not.toBe("accent");
    }
  });
});
