import type { ReactNode } from "react";
import { fmtCost, fmtDuration, fmtJson, prettyJson } from "../lib/format";
import type { GraphFallback, GraphStep } from "../lib/layout";
import type { RunEvent, Step } from "../types";
import { Empty, JsonBlock, Label, StatusChip } from "./ui";

type Data = Record<string, unknown>;

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="px-4 py-3">
      <Label>{title}</Label>
      <div className="mt-2">{children}</div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className="mono mt-1 text-[12px] text-muted">{value}</div>
    </div>
  );
}

function Block({ label, text, fail = false }: { label: string; text: string; fail?: boolean }) {
  return (
    <div className="mt-2">
      <div className="text-[10px] uppercase tracking-wider text-faint">{label}</div>
      <pre
        className={`mono mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-words border border-line bg-bg p-2 text-[11.5px] leading-relaxed ${
          fail ? "text-fail" : "text-muted"
        }`}
      >
        {text}
      </pre>
    </div>
  );
}

function CallView({ event }: { event: RunEvent }) {
  const d: Data = event.data;
  const llm = event.type === "llm.call";
  const usage = (d.usage ?? {}) as Data;
  const title = llm
    ? `LLM · ${String(d.model ?? "")}${d.mock ? " (mock)" : ""}${d.repair ? ` · repair ${String(d.repair)}` : ""}`
    : d.method
      ? `HTTP · ${String(d.method)} ${String(d.path ?? "")} → ${String(d.status ?? "error")}`
      : `SQL · ${String(d.operation ?? "query")}`;
  return (
    <Section title={title}>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11.5px] text-faint">
        <span className={d.ok === false ? "text-fail" : "text-ok"}>{d.ok === false ? "failed" : "ok"}</span>
        {d.duration_ms != null && <span className="mono">{fmtDuration(Number(d.duration_ms) / 1000)}</span>}
        {llm && usage.input_tokens != null && (
          <span className="mono">
            {String(usage.input_tokens)} in · {String(usage.output_tokens)} out
          </span>
        )}
        {Number(d.cost_usd) > 0 && <span className="mono">{fmtCost(Number(d.cost_usd))}</span>}
        {d.rows != null && <span className="mono">{String(d.rows)} rows</span>}
        {d.rowcount != null && <span className="mono">{String(d.rowcount)} rows changed</span>}
      </div>
      {llm ? (
        <>
          {d.system ? <Block label="system" text={String(d.system)} /> : null}
          <Block label="prompt" text={String(d.prompt ?? "")} />
          {d.response != null && <Block label="response" text={prettyJson(String(d.response))} />}
          {Array.isArray(d.errors) && d.errors.length > 0 && <Block label="schema errors" text={d.errors.join("\n")} fail />}
        </>
      ) : (
        <>
          {d.sql ? <Block label="sql" text={String(d.sql).trim()} /> : null}
          {d.params ? <Block label="params" text={fmtJson(d.params)} /> : null}
          {d.request ? <Block label="request" text={fmtJson(d.request)} /> : null}
          {d.response ? <Block label="response" text={fmtJson(d.response)} /> : null}
        </>
      )}
      {d.error ? <Block label="error" text={String(d.error)} fail /> : null}
    </Section>
  );
}

export function StepInspector({
  step,
  definition,
  events,
}: {
  step?: Step;
  definition?: GraphStep | GraphFallback;
  events: RunEvent[];
}) {
  if (!step) return <Empty>select a step in the graph or the trace</Empty>;
  const calls = events.filter((e) => e.type === "llm.call" || e.type === "connector.call");
  const failures = events.filter((e) => e.type === "step.retrying" || e.type === "step.failed");
  const when = definition && "when" in definition ? definition.when : null;
  return (
    <div className="divide-y divide-line">
      <div className="px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <span className="mono truncate text-[14px] text-text">{step.step_id}</span>
          <StatusChip status={step.status} kind="step" />
        </div>
        {definition?.description && <p className="mt-1 text-[12px] text-muted">{definition.description}</p>}
        <div className="mt-3 grid grid-cols-4 gap-3">
          <Fact label="type" value={definition?.type ?? "—"} />
          <Fact label="attempts" value={step.attempts} />
          <Fact label="duration" value={fmtDuration(step.duration_s) || "—"} />
          <Fact label="cost" value={fmtCost(step.cost_usd) || "—"} />
        </div>
        {when && <div className="mono mt-3 text-[11px] text-wait">when {when}</div>}
      </div>
      {step.error && (
        <Section title="Error">
          <pre className="mono whitespace-pre-wrap break-words text-[12px] text-fail">{step.error}</pre>
        </Section>
      )}
      {failures.length > 0 && (
        <Section title="Failed attempts">
          <ul className="space-y-1.5">
            {failures.map((e) => (
              <li key={e.seq} className="mono text-[11.5px] text-muted">
                <span className="text-faint">#{String(e.data.attempt ?? "?")}</span> {String(e.data.kind ?? "error")} ·{" "}
                {String(e.data.error ?? "")}
                {e.type === "step.retrying" && e.data.delay_s != null && (
                  <span className="text-wait"> · retried after {fmtDuration(Number(e.data.delay_s))}</span>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}
      <Section title="Output">
        {step.output == null ? <span className="text-faint">—</span> : <JsonBlock value={step.output} className="max-h-64" />}
      </Section>
      {calls.map((call) => (
        <CallView key={call.seq} event={call} />
      ))}
    </div>
  );
}
