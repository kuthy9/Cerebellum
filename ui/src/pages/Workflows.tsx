import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { api } from "../api";
import { NewRunDialog } from "../components/NewRunDialog";
import { RunGraph } from "../components/RunGraph";
import { Button, Empty, PageHeader, Tabs } from "../components/ui";

const VIEWS = ["graph", "yaml"] as const;

export function Workflows() {
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState<(typeof VIEWS)[number]>("graph");
  const [dialog, setDialog] = useState(false);
  const list = useQuery({ queryKey: ["workflows"], queryFn: api.workflows });
  const selectedId = params.get("id") ?? list.data?.[0]?.id ?? null;
  const detail = useQuery({
    queryKey: ["workflows", selectedId],
    queryFn: () => api.workflow(selectedId!),
    enabled: selectedId != null,
  });
  return (
    <div className="flex h-full flex-col">
      <PageHeader title="Workflows" subtitle="definitions on disk and from run history" />
      <div className="grid min-h-0 flex-1 grid-cols-[300px_minmax(0,1fr)]">
        <div className="overflow-auto border-r border-line">
          {list.data?.map((workflow) => (
            <button
              key={workflow.id}
              type="button"
              onClick={() => setParams({ id: workflow.id })}
              className={`block w-full border-b border-line px-4 py-3 text-left ${workflow.id === selectedId ? "bg-raised" : "hover:bg-raised/60"}`}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="mono truncate text-[12.5px] text-text">{workflow.name}</span>
                <span className="mono text-[11px] text-faint">v{workflow.version}</span>
              </div>
              <div className="mt-1 truncate text-[11px] text-faint">{workflow.source === "file" ? workflow.id : `run history · ${workflow.digest}`}</div>
              <div className="mt-1 text-[11px] text-muted">
                {workflow.steps} steps · {workflow.runs} runs
              </div>
            </button>
          ))}
          {list.data && !list.data.length && (
            <Empty>
              no workflows found — run <span className="mono">cerebellum init</span> here or start a run
            </Empty>
          )}
        </div>
        <div className="flex min-h-[520px] min-w-0 flex-col">
          {detail.data ? (
            <>
              <div className="flex items-start justify-between gap-4 border-b border-line px-6 py-3">
                <div className="min-w-0">
                  <div className="text-[15px] text-text">
                    {detail.data.name} <span className="mono text-[12px] text-faint">v{detail.data.version}</span>
                  </div>
                  {detail.data.description && <p className="mt-1 max-w-3xl text-[12px] text-muted">{detail.data.description}</p>}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Tabs value={view} options={VIEWS} onChange={setView} />
                  <Button tone="accent" onClick={() => setDialog(true)}>
                    New run
                  </Button>
                </div>
              </div>
              <div className="min-h-0 flex-1">
                {view === "graph" ? (
                  <RunGraph key={detail.data.id} graph={detail.data.graph} />
                ) : (
                  <pre className="mono h-full overflow-auto p-6 text-[12px] leading-relaxed text-muted">{detail.data.yaml}</pre>
                )}
              </div>
              {dialog && <NewRunDialog workflow={detail.data} onClose={() => setDialog(false)} />}
            </>
          ) : (
            <Empty>{list.isLoading || detail.isLoading ? "loading…" : "select a workflow"}</Empty>
          )}
        </div>
      </div>
    </div>
  );
}
