import { fmtDuration } from "../lib/format";
import { SPAN_TONE, toneColor } from "../lib/status";
import { layoutWaterfall, type Span } from "../lib/waterfall";
import { Empty, Panel } from "./ui";

export function Waterfall({
  spans,
  now,
  selected,
  onSelect,
}: {
  spans: Span[];
  now: number;
  selected?: string | null;
  onSelect?: (stepId: string) => void;
}) {
  const rows = layoutWaterfall(spans, now);
  const total = rows.length ? Math.max(...rows.map((r) => r.span.end ?? now)) - Math.min(...rows.map((r) => r.span.start)) : 0;
  return (
    <Panel title="Trace" actions={<span className="mono text-[11px] text-faint">{rows.length} spans · {fmtDuration(total)}</span>}>
      {rows.length === 0 ? (
        <Empty>no spans recorded</Empty>
      ) : (
        <div className="py-1">
          {rows.map((row) => {
            const stepId = row.span.step_id;
            const active = row.depth === 0 && stepId === selected;
            return (
              <button
                key={row.span.span_id}
                type="button"
                onClick={() => stepId && onSelect?.(stepId)}
                className={`grid h-6 w-full grid-cols-[240px_minmax(0,1fr)_64px] items-center gap-3 px-3 text-left hover:bg-raised ${
                  active ? "bg-raised" : ""
                }`}
              >
                <span className={`mono truncate text-[11.5px] ${row.depth ? "pl-4 text-muted" : "text-text"}`}>
                  {row.depth ? "└ " : ""}
                  {row.span.label}
                </span>
                <span className="relative h-2">
                  <span
                    className="absolute top-0 h-full"
                    style={{
                      left: `${row.offset}%`,
                      width: `${row.width}%`,
                      background: toneColor(SPAN_TONE[row.span.status] ?? "accent"),
                      opacity: row.depth ? 0.55 : 0.9,
                    }}
                  />
                </span>
                <span className="mono text-right text-[11px] text-faint">{fmtDuration(row.duration)}</span>
              </button>
            );
          })}
        </div>
      )}
    </Panel>
  );
}
