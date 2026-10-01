import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { RunsTable } from "../components/RunsTable";
import { Label, PageHeader, Panel } from "../components/ui";
import { useNow } from "../hooks/useNow";
import { fmtCost, fmtDuration, fmtPercent } from "../lib/format";
import { type Tone, toneColor } from "../lib/status";

export function Overview() {
  const now = useNow();
  const metrics = useQuery({ queryKey: ["metrics", "24h"], queryFn: () => api.metrics("24h") });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api.runs() });
  const m = metrics.data;
  const kpis: [string, string, Tone | null][] = [
    ["Runs · 24h", m ? String(m.runs) : "—", null],
    ["Success rate", fmtPercent(m?.success_rate), null],
    ["Avg duration", m?.avg_duration_s == null ? "—" : fmtDuration(m.avg_duration_s), null],
    ["Cost · 24h", m ? fmtCost(m.cost_usd) || "$0" : "—", null],
    ["Pending approvals", m ? String(m.pending_approvals) : "—", m && m.pending_approvals > 0 ? "wait" : null],
    ["Open tasks", m ? String(m.open_tasks) : "—", m && m.open_tasks > 0 ? "fallback" : null],
    ["Retries / fallbacks", m ? `${m.retries} / ${m.fallbacks}` : "—", null],
  ];
  return (
    <div>
      <PageHeader title="Overview" subtitle="last 24 hours · updates live" />
      <div className="grid grid-cols-2 border-b border-line md:grid-cols-4 xl:grid-cols-7">
        {kpis.map(([label, value, tone]) => (
          <div key={label} className="border-r border-b border-line px-5 py-4 xl:border-b-0">
            <Label>{label}</Label>
            <div className="mono mt-2 text-[22px] font-medium" style={tone ? { color: toneColor(tone) } : undefined}>
              {value}
            </div>
          </div>
        ))}
      </div>
      <div className="p-6">
        <Panel title="Recent runs">
          <RunsTable runs={runs.data ?? []} now={now} loading={runs.isLoading} />
        </Panel>
      </div>
    </div>
  );
}
