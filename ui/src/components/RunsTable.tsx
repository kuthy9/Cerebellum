import { Link, useNavigate } from "react-router";
import { fmtAge, fmtCost, fmtDuration } from "../lib/format";
import type { Run } from "../types";
import { Empty, StatusChip } from "./ui";

const HEADINGS = ["Run", "Workflow", "Status", "Duration", "Cost", "Age", "AI"];

export function RunsTable({ runs, now, loading }: { runs: Run[]; now: number; loading?: boolean }) {
  const navigate = useNavigate();
  if (loading) return <Empty>loading…</Empty>;
  if (!runs.length) {
    return (
      <Empty>
        no runs yet — start one from Workflows or run <span className="mono">cerebellum demo</span>
      </Empty>
    );
  }
  return (
    <table className="w-full text-left text-[12.5px]">
      <thead>
        <tr className="border-b border-line">
          {HEADINGS.map((heading) => (
            <th key={heading} className="label h-8 px-3 font-medium">
              {heading}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {runs.map((run) => (
          <tr
            key={run.run_id}
            onClick={() => navigate(`/runs/${run.run_id}`)}
            className="h-9 cursor-pointer border-b border-line last:border-b-0 hover:bg-raised"
          >
            <td className="px-3">
              <Link to={`/runs/${run.run_id}`} className="mono text-accent hover:underline" onClick={(e) => e.stopPropagation()}>
                {run.run_id}
              </Link>
            </td>
            <td className="px-3">{run.workflow_name}</td>
            <td className="px-3">
              <StatusChip status={run.status} />
              {run.stale && <span className="ml-2 text-[11px] text-fail">stale</span>}
            </td>
            <td className="mono px-3 text-muted">{fmtDuration(run.duration_s ?? now - run.created_at)}</td>
            <td className="mono px-3 text-muted">{fmtCost(run.cost_usd)}</td>
            <td className="px-3 text-faint">{fmtAge(run.created_at, now)}</td>
            <td className="px-3 text-faint">{run.mock ? "mock" : "claude"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
