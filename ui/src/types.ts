import type { Graph } from "./lib/layout";
import type { InputField } from "./lib/runs";
import type { RunStatus, StepStatus } from "./lib/status";
import type { Span } from "./lib/waterfall";

export interface Info {
  version: string;
  mode: string;
  mock: boolean;
  /** The mock AI was asked for (--mock / CEREBELLUM_MOCK) rather than a missing-key fallback. */
  mock_requested: boolean;
  model: string;
}

export interface Run {
  run_id: string;
  workflow_digest: string;
  workflow_name: string;
  status: RunStatus;
  input: Record<string, unknown>;
  params: Record<string, unknown>;
  output: unknown;
  error: string | null;
  cost_usd: number;
  mock: boolean;
  created_at: number;
  updated_at: number;
  ended_at: number | null;
  duration_s: number | null;
  stale: boolean;
  eval_run_id: string | null;
}

export interface Step {
  run_id: string;
  step_id: string;
  status: StepStatus;
  attempts: number;
  output: unknown;
  error: string | null;
  started_at: number | null;
  ended_at: number | null;
  cost_usd: number;
  duration_s: number | null;
}

export interface Approval {
  id: string;
  run_id: string;
  step_id: string;
  status: "pending" | "approved" | "rejected";
  title: string;
  context: { input?: Record<string, unknown>; steps?: Record<string, unknown> };
  requested_at: number;
  expires_at: number | null;
  on_timeout: string;
  decided_at: number | null;
  decided_by: string | null;
  comment: string | null;
  workflow_name?: string;
  run_status?: RunStatus;
}

export interface Task {
  id: string;
  run_id: string;
  step_id: string;
  title: string;
  assignee: string;
  payload: Record<string, unknown>;
  status: "open" | "resolved";
  created_at: number;
  resolved_at: number | null;
  resolved_by: string | null;
  note: string | null;
  workflow_name?: string;
}

export interface RunEvent {
  seq: number;
  run_id: string;
  step_id: string | null;
  span_id: string | null;
  parent_span_id: string | null;
  type: string;
  ts: number;
  data: Record<string, unknown>;
}

export interface RunDetail {
  run: Run;
  steps: Step[];
  graph: Graph | null;
  approvals: Approval[];
  tasks: Task[];
  spans: Span[];
}

export interface Metrics {
  since: number;
  window: string;
  runs: number;
  by_status: Record<string, number>;
  success_rate: number | null;
  avg_duration_s: number | null;
  cost_usd: number;
  pending_approvals: number;
  open_tasks: number;
  retries: number;
  fallbacks: number;
}

export interface WorkflowSummary {
  id: string;
  name: string;
  version: number;
  description: string;
  source: "file" | "history";
  path: string | null;
  digest: string;
  steps: number;
  runs: number;
  last_run_at: number | null;
}

export interface WorkflowDetail extends WorkflowSummary {
  yaml: string;
  graph: Graph;
  params: Record<string, unknown>;
  input: Record<string, InputField>;
  samples: Record<string, Record<string, unknown>>;
}

export interface EvalRun {
  id: string;
  suite: string;
  suite_path: string;
  workflow_name: string;
  workflow_digest: string;
  status: "running" | "completed" | "errored";
  mock: boolean;
  total: number;
  passed: number;
  failed: number;
  regressions: number;
  cost_usd: number;
  ai_first_try: number;
  ai_first_ok: number;
  ai_repairs: number;
  baseline_id: string | null;
  error: string | null;
  created_at: number;
  ended_at: number | null;
  pass_rate: number | null;
  duration_s: number | null;
  ai_first_pass_rate: number | null;
}

export interface EvalCheck {
  kind: "expect" | "assert";
  target: string;
  passed: boolean;
  expected: unknown;
  actual: unknown;
  missing: boolean;
}

export interface EvalResult {
  eval_run_id: string;
  case_id: string;
  position: number;
  run_id: string | null;
  passed: boolean;
  baseline_passed: boolean | null;
  checks: EvalCheck[];
  error: string | null;
  cost_usd: number;
  duration_s: number | null;
  regression: boolean;
}

export interface EvalDetail {
  eval: EvalRun;
  baseline: EvalRun | null;
  results: EvalResult[];
  history: EvalRun[];
}
