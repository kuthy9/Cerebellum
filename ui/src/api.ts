import type {
  Approval,
  EvalDetail,
  EvalRun,
  Info,
  Metrics,
  Run,
  RunDetail,
  RunEvent,
  Task,
  WorkflowDetail,
  WorkflowSummary,
} from "./types";

export interface Issue {
  path: string;
  message: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly issues: Issue[];

  constructor(status: number, message: string, issues: Issue[] = []) {
    super(message);
    this.status = status;
    this.issues = issues;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { ...init, headers: { "Content-Type": "application/json" } });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    let issues: Issue[] = [];
    try {
      const { detail } = await response.json();
      if (typeof detail === "string") message = detail;
      else if (Array.isArray(detail)) message = detail.map((d) => `${(d.loc ?? []).join(".")}: ${d.msg}`).join("; ");
      else if (detail && typeof detail === "object") {
        message = detail.message ?? message;
        issues = detail.issues ?? [];
      }
    } catch {
      // not a JSON error body; keep the status line
    }
    throw new ApiError(response.status, message, issues);
  }
  return (await response.json()) as T;
}

const post = <T>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });
const workflowPath = (id: string) => id.split("/").map(encodeURIComponent).join("/");

export const api = {
  info: () => request<Info>("/info"),
  runs: (status?: string) => request<{ runs: Run[] }>(status ? `/runs?status=${status}` : "/runs").then((r) => r.runs),
  run: (id: string) => request<RunDetail>(`/runs/${id}`),
  events: (id: string) => request<{ events: RunEvent[] }>(`/runs/${id}/events`).then((r) => r.events),
  startRun: (workflow: string, input: unknown, params: unknown) =>
    post<{ run: Run }>("/runs", { workflow, input, params }).then((r) => r.run),
  resume: (id: string) => post<{ run: Run }>(`/runs/${id}/resume`, {}).then((r) => r.run),
  approvals: (status: "pending" | "all") =>
    request<{ approvals: Approval[] }>(`/approvals?status=${status}`).then((r) => r.approvals),
  decide: (id: string, approved: boolean, by: string, comment: string) =>
    post<{ approval: Approval }>(`/approvals/${id}/decision`, { approved, by, comment }).then((r) => r.approval),
  tasks: (status: "open" | "all") => request<{ tasks: Task[] }>(`/tasks?status=${status}`).then((r) => r.tasks),
  resolveTask: (id: string, by: string, note: string) =>
    post<{ task: Task }>(`/tasks/${id}/resolve`, { by, note }).then((r) => r.task),
  metrics: (window = "24h") => request<Metrics>(`/metrics?window=${window}`),
  workflows: () => request<{ workflows: WorkflowSummary[] }>("/workflows").then((r) => r.workflows),
  workflow: (id: string) => request<WorkflowDetail>(`/workflows/${workflowPath(id)}`),
  evals: () => request<{ evals: EvalRun[] }>("/evals").then((r) => r.evals),
  evalRun: (id: string) => request<EvalDetail>(`/evals/${encodeURIComponent(id)}`),
};
