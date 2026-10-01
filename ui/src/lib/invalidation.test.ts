import { describe, expect, it } from "vitest";
import { keysFor } from "./invalidation";

describe("keysFor", () => {
  it("refreshes run lists, metrics and the run itself for every event", () => {
    expect(keysFor({ type: "step.succeeded", run_id: "r_1" })).toEqual([["runs"], ["metrics"], ["run", "r_1"]]);
  });

  it("refreshes approvals, tasks and workflows when they can change", () => {
    expect(keysFor({ type: "approval.requested", run_id: "r_1" })).toContainEqual(["approvals"]);
    expect(keysFor({ type: "run.suspended", run_id: "r_1" })).toContainEqual(["approvals"]);
    expect(keysFor({ type: "task.created", run_id: "r_1" })).toContainEqual(["tasks"]);
    expect(keysFor({ type: "run.started", run_id: "r_1" })).toContainEqual(["workflows"]);
    expect(keysFor({ type: "step.started", run_id: "r_1" })).not.toContainEqual(["tasks"]);
    expect(keysFor({ type: "run.completed", run_id: "r_1" })).toContainEqual(["evals"]);
    expect(keysFor({ type: "step.started", run_id: "r_1" })).not.toContainEqual(["evals"]);
  });
});
