import type { EvalCheck, EvalResult, EvalRun } from "../types";

export interface SuiteSummary {
  suite: string;
  workflow: string;
  latest: EvalRun;
  /** Oldest first, for trend lines. */
  runs: EvalRun[];
}

/** Group eval runs (newest first, as the API lists them) by suite, most recently run suite first. */
export function groupBySuite(runs: EvalRun[]): SuiteSummary[] {
  const groups = new Map<string, EvalRun[]>();
  for (const run of runs) {
    const list = groups.get(run.suite) ?? [];
    list.push(run);
    groups.set(run.suite, list);
  }
  return [...groups.entries()].map(([suite, list]) => ({
    suite,
    workflow: list[0].workflow_name,
    latest: list[0],
    runs: [...list].reverse(),
  }));
}

export interface Point {
  x: number;
  y: number;
  index: number;
}

/** Scale values into a width × height box for a sparkline (SVG y grows downward). Nulls are
 * skipped but keep their slot on the x axis; `max` pins the top of the scale (1 for rates). */
export function sparkPoints(values: (number | null)[], width: number, height: number, max?: number): Point[] {
  const present = values.filter((value): value is number => value != null);
  if (!present.length) return [];
  const top = max ?? Math.max(...present);
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  return values.flatMap((value, index) =>
    value == null
      ? []
      : [
          {
            x: values.length > 1 ? index * step : width / 2,
            y: top > 0 ? height - (value / top) * height : height,
            index,
          },
        ],
  );
}

const show = (value: unknown) => (typeof value === "string" ? value : JSON.stringify(value));

/** One line for a failed check: what the case expected and what the run did. */
export function describeCheck(check: EvalCheck): string {
  if (check.kind === "assert") return `assert ${check.target} → ${check.actual == null ? "false" : show(check.actual)}`;
  if (check.missing) return `${check.target}: expected ${show(check.expected)} · missing`;
  return `${check.target}: expected ${show(check.expected)} · got ${show(check.actual)}`;
}

/** The AI an eval ran with. An eval is compared only with a baseline of the same mode. */
export function aiMode(run: Pick<EvalRun, "mock">): "mock" | "claude" {
  return run.mock ? "mock" : "claude";
}

export type CaseChange = "regression" | "fixed" | "new" | null;

/** How a case moved against the baseline eval run (null without a baseline or without change). */
export function caseChange(result: EvalResult, hasBaseline: boolean): CaseChange {
  if (!hasBaseline) return null;
  if (result.baseline_passed === null) return "new";
  if (result.baseline_passed && !result.passed) return "regression";
  if (!result.baseline_passed && result.passed) return "fixed";
  return null;
}
