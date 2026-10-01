import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError, api } from "../api";
import { ApprovalCard } from "../components/ApprovalCard";
import { RunGraph } from "../components/RunGraph";
import { StepInspector } from "../components/StepInspector";
import { Button, Empty, ErrorNote, JsonBlock, Label, Meta, PageHeader, Panel, StatusChip } from "../components/ui";
import { Waterfall } from "../components/Waterfall";
import { useNow } from "../hooks/useNow";
import { fmtCost, fmtDuration } from "../lib/format";
import { defaultFocus, isResumable } from "../lib/runs";

export function RunDetail() {
  const { runId = "" } = useParams();
  const now = useNow();
  const client = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const detail = useQuery({ queryKey: ["run", runId], queryFn: () => api.run(runId) });
  const events = useQuery({ queryKey: ["run", runId, "events"], queryFn: () => api.events(runId) });
  const resume = useMutation({
    mutationFn: () => api.resume(runId),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["run", runId] }),
  });

  if (detail.isLoading) return <Empty>loading…</Empty>;
  if (!detail.data) {
    const missing = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div>
        <PageHeader title="Run" />
        <Empty>{missing ? `run ${runId} not found` : <ErrorNote error={detail.error} />}</Empty>
      </div>
    );
  }

  const { run, steps, graph, approvals, spans } = detail.data;
  const stepMap = Object.fromEntries(steps.map((s) => [s.step_id, s]));
  const focus = selected ?? defaultFocus(steps);
  const definitions = graph ? [...graph.steps, ...graph.fallbacks] : [];
  const pending = approvals.filter((a) => a.status === "pending");

  return (
    <div>
      <PageHeader
        title={<span className="mono">{run.run_id}</span>}
        subtitle={
          <Link to="/workflows" className="hover:text-muted">
            {run.workflow_name}
          </Link>
        }
        actions={
          isResumable(run) ? (
            <Button tone="accent" disabled={resume.isPending} onClick={() => resume.mutate()}>
              {resume.isPending ? "Resuming…" : "Resume"}
            </Button>
          ) : undefined
        }
      />
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-line px-6 py-2.5">
        <StatusChip status={run.status} />
        {run.stale && <span className="text-[12px] text-fail">stale — the process driving it stopped</span>}
        <Meta label="duration">{fmtDuration(run.duration_s ?? now - run.created_at)}</Meta>
        <Meta label="cost">{fmtCost(run.cost_usd) || "$0"}</Meta>
        <Meta label="ai">{run.mock ? "mock" : "claude"}</Meta>
        <Meta label="started">{new Date(run.created_at * 1000).toLocaleString()}</Meta>
        {run.eval_run_id && (
          <Meta label="eval">
            <Link to={`/evals/${run.eval_run_id}`} className="text-accent hover:underline">
              {run.eval_run_id}
            </Link>
          </Meta>
        )}
      </div>
      {run.error && <div className="mono border-b border-line px-6 py-2 text-[12px] text-fail">{run.error}</div>}
      {resume.error && (
        <div className="border-b border-line px-6 py-2">
          <ErrorNote error={resume.error} />
        </div>
      )}
      <div className="grid h-[480px] grid-cols-[minmax(0,3fr)_minmax(340px,2fr)] border-b border-line">
        <div className="min-w-0 border-r border-line">
          {graph ? (
            <RunGraph graph={graph} steps={stepMap} selected={focus} onSelect={setSelected} />
          ) : (
            <Empty>workflow graph unavailable for this run</Empty>
          )}
        </div>
        <div className="min-h-0 overflow-auto">
          {pending.map((approval) => (
            <ApprovalCard key={approval.id} approval={approval} compact />
          ))}
          <StepInspector
            step={focus ? stepMap[focus] : undefined}
            definition={definitions.find((d) => d.id === focus)}
            events={(events.data ?? []).filter((e) => e.step_id === focus)}
          />
        </div>
      </div>
      <div className="grid gap-6 p-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Waterfall spans={spans} now={now} selected={focus} onSelect={setSelected} />
        <Panel title="Run output">
          {run.output == null ? (
            <Empty>{run.status === "waiting_approval" ? "waiting for a human decision" : "no output yet"}</Empty>
          ) : (
            <JsonBlock value={run.output} className="p-3" />
          )}
          <div className="border-t border-line p-3">
            <Label>input</Label>
            <JsonBlock value={run.input} className="mt-1" />
          </div>
        </Panel>
      </div>
    </div>
  );
}
