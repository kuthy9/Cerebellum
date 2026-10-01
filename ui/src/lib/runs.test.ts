import { describe, expect, it } from "vitest";
import type { Step } from "../types";
import { defaultFocus, isResumable, skeletonInput } from "./runs";

const step = (step_id: string, status: Step["status"], started_at: number | null = null): Step => ({
  run_id: "r_1",
  step_id,
  status,
  attempts: started_at == null ? 0 : 1,
  output: null,
  error: null,
  started_at,
  ended_at: null,
  cost_usd: 0,
  duration_s: null,
});

describe("runs helpers", () => {
  it("focuses a step that needs attention first", () => {
    expect(defaultFocus([step("a", "succeeded", 1), step("b", "waiting", 2), step("c", "failed", 3)])).toBe("b");
    expect(defaultFocus([step("a", "succeeded", 1), step("b", "failed", 2)])).toBe("b");
  });

  it("otherwise focuses the step that ran last", () => {
    expect(defaultFocus([step("a", "succeeded", 1), step("b", "succeeded", 2), step("c", "pending")])).toBe("b");
    expect(defaultFocus([step("a", "pending")])).toBe("a");
    expect(defaultFocus([])).toBeNull();
  });

  it("builds an input skeleton from the input schema", () => {
    const f = (type: string, choices: unknown[] | null = null) => ({ type, required: true, enum: choices, description: "" });
    expect(skeletonInput({ id: f("string"), n: f("number"), ok: f("boolean"), kind: f("string", ["a", "b"]), tags: f("array") })).toEqual({
      id: "",
      n: 0,
      ok: false,
      kind: "a",
      tags: [],
    });
  });

  it("offers resume for failed or stale runs only", () => {
    expect(isResumable({ status: "failed", stale: false })).toBe(true);
    expect(isResumable({ status: "running", stale: true })).toBe(true);
    expect(isResumable({ status: "succeeded", stale: false })).toBe(false);
    expect(isResumable({ status: "failed", stale: false, eval_run_id: "ev_1" })).toBe(false);
    expect(isResumable({ status: "running", stale: true, eval_run_id: "ev_1" })).toBe(false);
  });
});
