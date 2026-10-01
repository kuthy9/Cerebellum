import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { ApiError, api } from "../api";
import { Sparkline } from "../components/Sparkline";
import { Empty, ErrorNote, Meta, PageHeader, Panel } from "../components/ui";
import { useNow } from "../hooks/useNow";
import { type CaseChange, aiMode, caseChange, describeCheck, elapsed, isLive } from "../lib/evals";
import { fmtCost, fmtDuration, fmtPercent } from "../lib/format";
import { type Tone, toneColor } from "../lib/status";
import type { EvalResult } from "../types";
import { EvalOutcome } from "./Evals";

const CHANGE: Record<Exclude<CaseChange, null>, [string, Tone]> = {
  regression: ["↓ regression", "fail"],
  fixed: ["↑ fixed", "ok"],
  new: ["new", "faint"],
};

function CaseRow({ result, hasBaseline }: { result: EvalResult; hasBaseline: boolean }) {
  const change = caseChange(result, hasBaseline);
  const failed = result.checks.filter((check) => !check.passed);
  return (
    <div className="border-b border-line px-3 py-2.5 last:border-b-0">
      <div className="flex items-center gap-3 text-[12.5px]">
        <span className="mono w-4" style={{ color: toneColor(result.passed ? "ok" : "fail") }}>
          {result.passed ? "●" : "✕"}
        </span>
        <span className="min-w-0 flex-1 truncate">{result.case_id}</span>
        {change && (
          <span className="mono text-[11px]" style={{ color: toneColor(CHANGE[change][1]) }}>
            {CHANGE[change][0]}
          </span>
        )}
        {result.run_id ? (
          <Link to={`/runs/${result.run_id}`} className="mono text-accent hover:underline">
            {result.run_id}
          </Link>
        ) : (
          <span className="mono text-faint">no run</span>
        )}
        <span className="mono w-16 text-right text-muted">{fmtDuration(result.duration_s)}</span>
      </div>
      {(failed.length > 0 || result.error) && (
        <div className="mono mt-1.5 space-y-0.5 pl-7 text-[11.5px] text-fail">
          {result.error && <div>{result.error}</div>}
          {failed.map((check) => (
            <div key={`${check.kind}:${check.target}`}>{describeCheck(check)}</div>
          ))}
        </div>
      )}
    </div>
  );
}

export function EvalDetail() {
  const { evalId = "" } = useParams();
  const now = useNow();
  const detail = useQuery({
    queryKey: ["evals", evalId],
    queryFn: () => api.evalRun(evalId),
    refetchInterval: (query) => (query.state.data && isLive(query.state.data.eval) ? 1500 : false),
  });

  if (detail.isLoading) return <Empty>loading…</Empty>;
  if (!detail.data) {
    const missing = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div>
        <PageHeader title="Eval" />
        <Empty>{missing ? `eval ${evalId} not found` : <ErrorNote error={detail.error} />}</Empty>
      </div>
    );
  }

  const { eval: run, baseline, results, history } = detail.data;
  const failing = results.filter((result) => !result.passed).length;
  return (
    <div>
      <PageHeader
        title={<span className="mono">{run.id}</span>}
        subtitle={
          <Link to="/evals" className="hover:text-muted">
            {run.suite} · {run.workflow_name}
          </Link>
        }
      />
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-line px-6 py-2.5">
        <EvalOutcome run={run} />
        <Meta label="pass rate">{fmtPercent(run.pass_rate)}</Meta>
        <Meta label="vs">
          {baseline ? (
            <>
              <Link to={`/evals/${baseline.id}`} className="text-accent hover:underline">
                {baseline.id}
              </Link>{" "}
              {baseline.passed}/{baseline.total} · {aiMode(baseline)}
            </>
          ) : (
            "first run"
          )}
        </Meta>
        <Meta label="regressions">
          <span style={{ color: run.regressions ? toneColor("fail") : undefined }}>{run.regressions}</span>
        </Meta>
        <Meta label="cost">{fmtCost(run.cost_usd) || "$0"}</Meta>
        <Meta label="duration">{fmtDuration(elapsed(run, now))}</Meta>
        <Meta label="ai first try">{run.ai_first_try ? `${run.ai_first_ok}/${run.ai_first_try}` : "—"}</Meta>
        <Meta label="repairs">{run.ai_repairs}</Meta>
        <Meta label="ai">{aiMode(run)}</Meta>
      </div>
      {run.status === "running" && run.stale && (
        <div className="border-b border-line px-6 py-2 text-[12px] text-fail">
          stale — the process running this eval stopped; the next eval of this suite records it as abandoned
        </div>
      )}
      {run.error && <div className="mono border-b border-line px-6 py-2 text-[12px] text-fail">{run.error}</div>}
      <div className="grid gap-6 p-6 xl:grid-cols-[minmax(0,3fr)_minmax(260px,1fr)]">
        <Panel title="Cases" actions={<span className="mono text-[11px] text-faint">{failing ? `${failing} failing` : "all passing"}</span>}>
          {results.length ? (
            results.map((result) => <CaseRow key={result.case_id} result={result} hasBaseline={baseline !== null} />)
          ) : (
            <Empty>no case has finished yet</Empty>
          )}
        </Panel>
        <Panel title="Pass rate history">
          <div className="p-3">
            <Sparkline
              values={history.map((h) => h.pass_rate)}
              max={1}
              highlight={history.findIndex((h) => h.id === run.id)}
              width={240}
              height={56}
              color={toneColor(run.failed ? "fail" : "ok")}
            />
            <div className="mt-3 space-y-1">
              {[...history].reverse().map((h) => (
                <div key={h.id} className="flex items-center justify-between text-[12px]">
                  <Link to={`/evals/${h.id}`} className={`mono hover:underline ${h.id === run.id ? "text-text" : "text-accent"}`}>
                    {h.id}
                  </Link>
                  <EvalOutcome run={h} />
                </div>
              ))}
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}
