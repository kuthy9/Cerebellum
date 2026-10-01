import { describe, expect, it } from "vitest";
import type { EvalCheck, EvalResult, EvalRun } from "../types";
import { aiMode, caseChange, describeCheck, groupBySuite, sparkPoints } from "./evals";

const run = (id: string, suite: string, extra: Partial<EvalRun> = {}): EvalRun => ({
  id,
  suite,
  suite_path: `/x/${suite}.yaml`,
  workflow_name: `${suite}_wf`,
  workflow_digest: "d",
  status: "completed",
  mock: true,
  total: 2,
  passed: 2,
  failed: 0,
  regressions: 0,
  cost_usd: 0,
  ai_first_try: 0,
  ai_first_ok: 0,
  ai_repairs: 0,
  baseline_id: null,
  error: null,
  created_at: 0,
  ended_at: 1,
  pass_rate: 1,
  duration_s: 1,
  ai_first_pass_rate: null,
  ...extra,
});

const result = (passed: boolean, baseline_passed: boolean | null): EvalResult => ({
  eval_run_id: "ev_1",
  case_id: "c",
  position: 0,
  run_id: "r_1",
  passed,
  baseline_passed,
  checks: [],
  error: null,
  cost_usd: 0,
  duration_s: 0.1,
  regression: baseline_passed === true && !passed,
});

describe("groupBySuite", () => {
  it("groups newest-first runs by suite, latest suite first, runs oldest first", () => {
    const groups = groupBySuite([run("ev_3", "b"), run("ev_2", "a"), run("ev_1", "b")]);
    expect(groups.map((g) => g.suite)).toEqual(["b", "a"]);
    expect(groups[0].latest.id).toBe("ev_3");
    expect(groups[0].runs.map((r) => r.id)).toEqual(["ev_1", "ev_3"]);
    expect(groups[0].workflow).toBe("b_wf");
  });
});

describe("sparkPoints", () => {
  it("scales values into the box with y growing downward", () => {
    expect(sparkPoints([0, 0.5, 1], 100, 20, 1)).toEqual([
      { x: 0, y: 20, index: 0 },
      { x: 50, y: 10, index: 1 },
      { x: 100, y: 0, index: 2 },
    ]);
  });

  it("skips nulls but keeps their slot, centres a single point and survives all-zero data", () => {
    expect(sparkPoints([null, 2], 10, 10)).toEqual([{ x: 10, y: 0, index: 1 }]);
    expect(sparkPoints([3], 10, 10)).toEqual([{ x: 5, y: 0, index: 0 }]);
    expect(sparkPoints([0, 0], 10, 10).map((p) => p.y)).toEqual([10, 10]);
    expect(sparkPoints([null], 10, 10)).toEqual([]);
  });
});

describe("describeCheck", () => {
  const base: EvalCheck = { kind: "expect", target: "output.decision", passed: false, expected: "refunded", actual: "manual", missing: false };
  it("shows expected and actual values", () => {
    expect(describeCheck(base)).toBe("output.decision: expected refunded · got manual");
    expect(describeCheck({ ...base, expected: 3, actual: 1, target: "steps.x.attempts" })).toBe("steps.x.attempts: expected 3 · got 1");
    expect(describeCheck({ ...base, missing: true, actual: null })).toBe("output.decision: expected refunded · missing");
  });
  it("shows why an assertion failed", () => {
    expect(describeCheck({ ...base, kind: "assert", target: "run.cost_usd < 1", actual: null })).toBe("assert run.cost_usd < 1 → false");
    expect(describeCheck({ ...base, kind: "assert", target: "'x' in error", actual: "TypeError: boom" })).toBe("assert 'x' in error → TypeError: boom");
  });
});

describe("aiMode", () => {
  it("names the AI an eval ran with, so a baseline's mode is visible", () => {
    expect(aiMode(run("ev_1", "a"))).toBe("mock");
    expect(aiMode(run("ev_2", "a", { mock: false }))).toBe("claude");
  });
});

describe("caseChange", () => {
  it("compares a case with the baseline run", () => {
    expect(caseChange(result(false, true), true)).toBe("regression");
    expect(caseChange(result(true, false), true)).toBe("fixed");
    expect(caseChange(result(true, null), true)).toBe("new");
    expect(caseChange(result(false, false), true)).toBeNull();
    expect(caseChange(result(false, null), false)).toBeNull();
  });
});
