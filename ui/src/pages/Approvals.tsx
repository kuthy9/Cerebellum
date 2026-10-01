import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { ApprovalCard } from "../components/ApprovalCard";
import { Empty, ErrorNote, PageHeader, Panel, Tabs } from "../components/ui";

const FILTERS = ["pending", "all"] as const;

export function Approvals() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("pending");
  const approvals = useQuery({ queryKey: ["approvals", filter], queryFn: () => api.approvals(filter) });
  return (
    <div>
      <PageHeader title="Approvals" subtitle="human-in-the-loop decisions" actions={<Tabs value={filter} options={FILTERS} onChange={setFilter} />} />
      <div className="p-6">
        <Panel>
          {approvals.isLoading ? (
            <Empty>loading…</Empty>
          ) : approvals.error ? (
            <Empty>
              <ErrorNote error={approvals.error} />
            </Empty>
          ) : approvals.data?.length ? (
            approvals.data.map((approval) => <ApprovalCard key={approval.id} approval={approval} />)
          ) : (
            <Empty>{filter === "pending" ? "nothing is waiting for a decision" : "no approvals yet"}</Empty>
          )}
        </Panel>
      </div>
    </div>
  );
}
