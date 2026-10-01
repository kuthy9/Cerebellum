import { useQuery } from "@tanstack/react-query";
import { NavLink, Outlet } from "react-router";
import { api } from "../api";
import { useEventStream } from "../hooks/useEventStream";
import { mockNotice } from "../lib/info";

const NAV = [
  { to: "/", label: "Overview", end: true, badge: null },
  { to: "/workflows", label: "Workflows", end: false, badge: null },
  { to: "/approvals", label: "Approvals", end: false, badge: "pending_approvals" },
  { to: "/tasks", label: "Tasks", end: false, badge: "open_tasks" },
  { to: "/evals", label: "Evals", end: false, badge: null },
] as const;

export function Shell() {
  const live = useEventStream();
  const info = useQuery({ queryKey: ["info"], queryFn: api.info, staleTime: Infinity });
  const metrics = useQuery({ queryKey: ["metrics", "24h"], queryFn: () => api.metrics("24h") });
  const notice = info.data ? mockNotice(info.data) : null;
  return (
    <div className="flex h-full">
      <aside className="flex w-52 shrink-0 flex-col border-r border-line">
        <div className="flex h-14 items-center gap-2.5 border-b border-line px-4">
          <span className="h-2 w-2 rounded-full bg-accent" />
          <span className="text-[12px] font-semibold tracking-[0.2em]">CEREBELLUM</span>
        </div>
        <nav className="flex flex-col py-2">
          {NAV.map((item) => {
            const count = item.badge && metrics.data ? metrics.data[item.badge] : 0;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `flex h-8 items-center justify-between border-l-2 px-4 text-[12.5px] ${
                    isActive ? "border-accent bg-raised text-text" : "border-transparent text-muted hover:text-text"
                  }`
                }
              >
                <span>{item.label}</span>
                {count > 0 && <span className="mono text-[11px] text-wait">{count}</span>}
              </NavLink>
            );
          })}
        </nav>
        <div className="mt-auto space-y-1.5 border-t border-line px-4 py-3 text-[11px]">
          <div className="flex items-center gap-2">
            <span className={`h-1.5 w-1.5 rounded-full ${live ? "bg-accent" : "bg-faint"}`} />
            <span className="text-muted">{live ? "live" : "reconnecting…"}</span>
          </div>
          {info.data && <div className={info.data.mock ? "text-wait" : "text-muted"}>{info.data.mode}</div>}
          {info.data && <div className="mono text-faint">v{info.data.version}</div>}
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-auto">
        {notice && <div className="border-b border-line px-6 py-1.5 text-[11.5px] text-wait">{notice}</div>}
        <Outlet />
      </main>
    </div>
  );
}
