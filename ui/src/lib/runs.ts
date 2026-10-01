import type { Step } from "../types";

const ATTENTION = ["waiting", "failed", "running", "retrying"] as const;

/** The step a person most likely wants to inspect: one that needs attention, else the last that ran. */
export function defaultFocus(steps: Step[]): string | null {
  for (const status of ATTENTION) {
    const step = steps.find((s) => s.status === status);
    if (step) return step.step_id;
  }
  const ran = steps.filter((s) => s.started_at != null);
  if (ran.length) return ran.reduce((a, b) => ((b.started_at ?? 0) >= (a.started_at ?? 0) ? b : a)).step_id;
  return steps[0]?.step_id ?? null;
}

export interface InputField {
  type: string;
  required: boolean;
  enum: unknown[] | null;
  description: string;
}

const BLANK: Record<string, unknown> = { string: "", number: 0, integer: 0, boolean: false, object: {}, array: [] };

/** A starting input for the new-run form when the workflow ships no sample inputs. */
export function skeletonInput(fields: Record<string, InputField>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(fields).map(([name, field]) => [name, field.enum?.length ? field.enum[0] : (BLANK[field.type] ?? "")]),
  );
}

/** Eval case runs are records of their eval and are never resumed from the dashboard. */
export function isResumable(run: { status: string; stale: boolean; eval_run_id?: string | null }): boolean {
  if (run.eval_run_id) return false;
  return run.status === "failed" || run.stale;
}
