import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Link } from "react-router";
import { api } from "../api";
import { useApprover } from "../hooks/useApprover";
import { useNow } from "../hooks/useNow";
import { fmtAge, fmtAmount, fmtDuration } from "../lib/format";
import type { Approval } from "../types";
import { Button, ErrorNote, JsonBlock, Label } from "./ui";

export function ApprovalCard({ approval, compact = false }: { approval: Approval; compact?: boolean }) {
  const [approver, setApprover] = useApprover();
  const [comment, setComment] = useState("");
  const now = useNow();
  const client = useQueryClient();
  const decide = useMutation({
    mutationFn: (approved: boolean) => api.decide(approval.id, approved, approver.trim(), comment),
    onSuccess: () => {
      for (const key of [["approvals"], ["run", approval.run_id], ["runs"], ["metrics"]]) {
        void client.invalidateQueries({ queryKey: key });
      }
    },
  });
  const input = approval.context.input ?? {};
  const shown = approval.context.steps ?? {};
  const facts = Object.entries(input).filter(([key]) => key !== "amount");
  const pending = approval.status === "pending";
  const overdue = approval.expires_at != null && approval.expires_at < now;
  return (
    <article className="border-b border-line px-4 py-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-[14px] text-text">{approval.title}</div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11.5px] text-faint">
            <Link to={`/runs/${approval.run_id}`} className="mono text-accent hover:underline">
              {approval.run_id}
            </Link>
            {approval.workflow_name && <span>{approval.workflow_name}</span>}
            <span className="mono">{approval.step_id}</span>
            <span>requested {fmtAge(approval.requested_at, now)}</span>
            {pending && approval.expires_at != null && (
              <span className={overdue ? "text-fail" : ""}>
                {overdue ? "overdue" : `expires in ${fmtDuration(approval.expires_at - now)}`} · on timeout {approval.on_timeout}
              </span>
            )}
          </div>
        </div>
        {"amount" in input && (
          <div className="shrink-0 text-right">
            <Label>amount</Label>
            <div className="mono text-[24px] leading-tight text-accent">{fmtAmount(input.amount)}</div>
          </div>
        )}
      </div>
      {!compact && facts.length > 0 && (
        <dl className="mt-3 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-[12px]">
          {facts.map(([key, value]) => (
            <Fragment key={key}>
              <dt className="text-faint">{key}</dt>
              <dd className="mono text-muted">{typeof value === "string" ? value : JSON.stringify(value)}</dd>
            </Fragment>
          ))}
        </dl>
      )}
      {Object.entries(shown).map(([stepId, output]) => (
        <details key={stepId} open={!compact} className="mt-3">
          <summary className="label cursor-pointer select-none">{stepId}</summary>
          <JsonBlock value={output} className="mt-1 max-h-56 border border-line bg-bg p-2" />
        </details>
      ))}
      {pending ? (
        <form className="mt-4 flex flex-wrap items-center gap-2" onSubmit={(e) => e.preventDefault()}>
          <input
            value={approver}
            onChange={(e) => setApprover(e.target.value)}
            placeholder="your name"
            aria-label="your name"
            className="input w-36"
          />
          <input
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="comment (optional)"
            aria-label="comment"
            className="input min-w-0 flex-1"
          />
          <Button tone="ok" disabled={!approver.trim() || decide.isPending} onClick={() => decide.mutate(true)}>
            Approve
          </Button>
          <Button tone="fail" disabled={!approver.trim() || decide.isPending} onClick={() => decide.mutate(false)}>
            Reject
          </Button>
        </form>
      ) : (
        <div className={`mt-3 text-[12px] ${approval.status === "approved" ? "text-ok" : "text-fail"}`}>
          {approval.status} by {approval.decided_by}
          {approval.comment ? ` · ${approval.comment}` : ""}
        </div>
      )}
      <div className="mt-2">
        <ErrorNote error={decide.error} />
      </div>
    </article>
  );
}
