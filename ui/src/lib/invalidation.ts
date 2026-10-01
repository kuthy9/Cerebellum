import type { RunEvent } from "../types";

/** The query keys an incoming event makes stale (prefix match in TanStack Query). */
export function keysFor(event: Pick<RunEvent, "type" | "run_id">): string[][] {
  const keys = [["runs"], ["metrics"], ["run", event.run_id]];
  if (event.type.startsWith("approval.") || event.type.startsWith("run.")) keys.push(["approvals"]);
  if (event.type.startsWith("task.")) keys.push(["tasks"]);
  if (event.type === "run.started") keys.push(["workflows"]);
  if (event.type.startsWith("run.")) keys.push(["evals"]);
  return keys;
}
