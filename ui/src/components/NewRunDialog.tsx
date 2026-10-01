import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { api, ApiError } from "../api";
import { fmtJson } from "../lib/format";
import { skeletonInput } from "../lib/runs";
import type { WorkflowDetail } from "../types";
import { Button, ErrorNote, Label } from "./ui";

export function NewRunDialog({ workflow, onClose }: { workflow: WorkflowDetail; onClose: () => void }) {
  const navigate = useNavigate();
  const names = Object.keys(workflow.samples);
  const [sample, setSample] = useState(names[0] ?? "");
  const [input, setInput] = useState(() => fmtJson(names.length ? workflow.samples[names[0]] : skeletonInput(workflow.input)));
  const [params, setParams] = useState(() => fmtJson(workflow.params));
  const [parseError, setParseError] = useState<string | null>(null);
  const start = useMutation({
    mutationFn: (body: { input: unknown; params: unknown }) => api.startRun(workflow.id, body.input, body.params),
    onSuccess: (run) => navigate(`/runs/${run.run_id}`),
  });

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const submit = () => {
    try {
      const body = { input: JSON.parse(input), params: params.trim() ? JSON.parse(params) : {} };
      setParseError(null);
      start.mutate(body);
    } catch (error) {
      setParseError(`invalid JSON: ${(error as Error).message}`);
    }
  };
  const issues = start.error instanceof ApiError ? start.error.issues : [];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        role="dialog"
        aria-label={`New run of ${workflow.name}`}
        className="w-[560px] max-w-[calc(100vw-32px)] border border-line-strong bg-panel"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex h-10 items-center justify-between border-b border-line px-4">
          <Label>New run · {workflow.name}</Label>
          <button type="button" onClick={onClose} className="text-faint hover:text-text" aria-label="close">
            ✕
          </button>
        </header>
        <div className="space-y-4 p-4">
          {names.length > 0 && (
            <div>
              <Label>Sample input</Label>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {names.map((name) => (
                  <button
                    key={name}
                    type="button"
                    onClick={() => {
                      setSample(name);
                      setInput(fmtJson(workflow.samples[name]));
                    }}
                    className={`mono h-7 border px-2.5 text-[11.5px] ${
                      name === sample ? "border-accent text-accent" : "border-line text-muted hover:text-text"
                    }`}
                  >
                    {name}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div>
            <Label>Input · JSON</Label>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              rows={8}
              spellCheck={false}
              aria-label="run input"
              className="input-area mono mt-1.5"
            />
          </div>
          <div>
            <Label>Params · JSON</Label>
            <textarea
              value={params}
              onChange={(e) => setParams(e.target.value)}
              rows={3}
              spellCheck={false}
              aria-label="run params"
              className="input-area mono mt-1.5"
            />
          </div>
          {parseError ? <ErrorNote error={parseError} /> : <ErrorNote error={start.error} />}
          {issues.length > 0 && (
            <ul className="mono space-y-0.5 text-[11.5px] text-fail">
              {issues.map((issue) => (
                <li key={`${issue.path}:${issue.message}`}>
                  {issue.path} — {issue.message}
                </li>
              ))}
            </ul>
          )}
        </div>
        <footer className="flex justify-end gap-2 border-t border-line px-4 py-3">
          <Button onClick={onClose}>Cancel</Button>
          <Button tone="accent" disabled={start.isPending} onClick={submit}>
            {start.isPending ? "Starting…" : "Start run"}
          </Button>
        </footer>
      </div>
    </div>
  );
}
