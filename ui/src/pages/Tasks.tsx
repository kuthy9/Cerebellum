import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../api";
import { Button, Empty, ErrorNote, JsonBlock, PageHeader, Panel, Tabs } from "../components/ui";
import { useApprover } from "../hooks/useApprover";
import { useNow } from "../hooks/useNow";
import { fmtAge } from "../lib/format";
import type { Task } from "../types";

const FILTERS = ["open", "all"] as const;

function TaskRow({ task, now }: { task: Task; now: number }) {
  const [name, setName] = useApprover();
  const [note, setNote] = useState("");
  const client = useQueryClient();
  const resolve = useMutation({
    mutationFn: () => api.resolveTask(task.id, name.trim(), note),
    onSuccess: () => {
      for (const key of [["tasks"], ["metrics"], ["run", task.run_id]]) void client.invalidateQueries({ queryKey: key });
    },
  });
  return (
    <article className="border-b border-line px-4 py-4 last:border-b-0">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-[14px] text-text">{task.title}</div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11.5px] text-faint">
            <span className="mono">{task.id}</span>
            <Link to={`/runs/${task.run_id}`} className="mono text-accent hover:underline">
              {task.run_id}
            </Link>
            {task.workflow_name && <span>{task.workflow_name}</span>}
            <span>assignee {task.assignee}</span>
            <span>opened {fmtAge(task.created_at, now)}</span>
          </div>
        </div>
        <span className={`text-[12px] ${task.status === "open" ? "text-fallback" : "text-ok"}`}>{task.status}</span>
      </div>
      <JsonBlock value={task.payload} className="mt-3 max-h-48 border border-line bg-bg p-2" />
      {task.status === "open" ? (
        <form className="mt-3 flex flex-wrap items-center gap-2" onSubmit={(e) => e.preventDefault()}>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="your name" aria-label="your name" className="input w-36" />
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="what was done" aria-label="resolution note" className="input min-w-0 flex-1" />
          <Button tone="ok" disabled={!name.trim() || resolve.isPending} onClick={() => resolve.mutate()}>
            Mark resolved
          </Button>
        </form>
      ) : (
        <div className="mt-3 text-[12px] text-ok">
          resolved by {task.resolved_by}
          {task.note ? ` · ${task.note}` : ""}
        </div>
      )}
      <div className="mt-2">
        <ErrorNote error={resolve.error} />
      </div>
    </article>
  );
}

export function Tasks() {
  const now = useNow();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("open");
  const tasks = useQuery({ queryKey: ["tasks", filter], queryFn: () => api.tasks(filter) });
  return (
    <div>
      <PageHeader title="Tasks" subtitle="manual work handed over by fallbacks" actions={<Tabs value={filter} options={FILTERS} onChange={setFilter} />} />
      <div className="p-6">
        <Panel>
          {tasks.isLoading ? (
            <Empty>loading…</Empty>
          ) : tasks.data?.length ? (
            tasks.data.map((task) => <TaskRow key={task.id} task={task} now={now} />)
          ) : (
            <Empty>{filter === "open" ? "no open tasks" : "no tasks yet"}</Empty>
          )}
        </Panel>
      </div>
    </div>
  );
}
