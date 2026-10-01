import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router";
import { api } from "../api";
import { Sparkline } from "../components/Sparkline";
import { Empty, ErrorNote, Label, PageHeader, Panel } from "../components/ui";
import { useNow } from "../hooks/useNow";
import { type SuiteSummary, groupBySuite } from "../lib/evals";
import { fmtAge, fmtCost, fmtDuration, fmtPercent } from "../lib/format";
import { toneColor } from "../lib/status";
import type { EvalRun } from "../types";

const HEADINGS = ["Eval", "Passed", "Regressions", "Cost", "Duration", "AI first try", "AI", "Age"];

export function EvalOutcome({ run }: { run: EvalRun }) {
  if (run.status === "running") return <span style={{ color: toneColor("accent") }}>◐ running · {run.passed + run.failed}/{run.total}</span>;
  if (run.status === "errored") return <span style={{ color: toneColor("fail") }}>✕ errored · {run.passed}/{run.total}</span>;
  const tone = run.failed ? "fail" : "ok";
  return (
    <span className="mono" style={{ color: toneColor(tone) }}>
      {run.passed}/{run.total}
    </span>
  );
}

function Trend({ label, value, values, max, color }: { label: string; value: string; values: (number | null)[]; max?: number; color: string }) {
  return (
    <div className="border-b border-line px-4 py-3 md:border-r md:border-b-0 md:last:border-r-0">
      <Label>{label}</Label>
      <div className="mt-1 flex items-end justify-between gap-3">
        <span className="mono text-[18px] font-medium">{value}</span>
        <Sparkline values={values} max={max} color={color} highlight={values.length - 1} />
      </div>
    </div>
  );
}

function SuitePanel({ summary, now }: { summary: SuiteSummary; now: number }) {
  const navigate = useNavigate();
  const { latest, runs } = summary;
  const recent = [...runs].reverse().slice(0, 10);
  return (
    <Panel
      title={`${summary.suite} · ${summary.workflow}`}
      actions={<span className="mono text-[11px] text-faint">{runs.length} runs</span>}
    >
      <div className="grid grid-cols-1 border-b border-line md:grid-cols-3">
        <Trend
          label="Pass rate"
          value={fmtPercent(latest.pass_rate)}
          values={runs.map((r) => r.pass_rate)}
          max={1}
          color={toneColor(latest.failed ? "fail" : "ok")}
        />
        <Trend label="Cost per eval" value={fmtCost(latest.cost_usd) || "$0"} values={runs.map((r) => r.cost_usd)} color={toneColor("accent")} />
        <Trend label="Duration" value={fmtDuration(latest.duration_s) || "—"} values={runs.map((r) => r.duration_s)} color={toneColor("accent")} />
      </div>
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
          {recent.map((run) => (
            <tr key={run.id} onClick={() => navigate(`/evals/${run.id}`)} className="h-9 cursor-pointer border-b border-line last:border-b-0 hover:bg-raised">
              <td className="px-3">
                <Link to={`/evals/${run.id}`} className="mono text-accent hover:underline" onClick={(e) => e.stopPropagation()}>
                  {run.id}
                </Link>
              </td>
              <td className="px-3">
                <EvalOutcome run={run} />
              </td>
              <td className="mono px-3" style={{ color: run.regressions ? toneColor("fail") : toneColor("faint") }}>
                {run.regressions ? `↓ ${run.regressions}` : "—"}
              </td>
              <td className="mono px-3 text-muted">{fmtCost(run.cost_usd) || "$0"}</td>
              <td className="mono px-3 text-muted">{fmtDuration(run.duration_s ?? now - run.created_at)}</td>
              <td className="mono px-3 text-muted">{fmtPercent(run.ai_first_pass_rate)}</td>
              <td className="px-3 text-faint">{run.mock ? "mock" : "claude"}</td>
              <td className="px-3 text-faint">{fmtAge(run.created_at, now)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

export function Evals() {
  const now = useNow();
  const evals = useQuery({
    queryKey: ["evals"],
    queryFn: api.evals,
    refetchInterval: (query) => (query.state.data?.some((run) => run.status === "running") ? 2000 : false),
  });
  const suites = groupBySuite(evals.data ?? []);
  return (
    <div>
      <PageHeader title="Evals" subtitle="regression suites · cerebellum eval <suite>" />
      <div className="space-y-6 p-6">
        {evals.isLoading ? (
          <Panel>
            <Empty>loading…</Empty>
          </Panel>
        ) : evals.error ? (
          <Panel>
            <Empty>
              <ErrorNote error={evals.error} />
            </Empty>
          </Panel>
        ) : suites.length ? (
          suites.map((summary) => <SuitePanel key={summary.suite} summary={summary} now={now} />)
        ) : (
          <Panel>
            <Empty>
              no eval runs yet — run <span className="mono">cerebellum eval workflows/refund/evals.yaml --mock</span>
            </Empty>
          </Panel>
        )}
      </div>
    </div>
  );
}
