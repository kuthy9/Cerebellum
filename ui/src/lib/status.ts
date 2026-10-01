export type StepStatus =
  | "pending"
  | "running"
  | "retrying"
  | "waiting"
  | "succeeded"
  | "failed"
  | "skipped"
  | "cancelled"
  | "recovered";
export type RunStatus = "running" | "waiting_approval" | "succeeded" | "failed" | "rejected" | "needs_attention";
export type Tone = "accent" | "ok" | "wait" | "fail" | "fallback" | "skip" | "faint";

export interface Look {
  glyph: string;
  tone: Tone;
  label: string;
}

export const STEP_LOOK: Record<StepStatus, Look> = {
  pending: { glyph: "○", tone: "faint", label: "pending" },
  running: { glyph: "◐", tone: "accent", label: "running" },
  retrying: { glyph: "↻", tone: "wait", label: "retrying" },
  waiting: { glyph: "⏸", tone: "wait", label: "awaiting approval" },
  succeeded: { glyph: "●", tone: "ok", label: "succeeded" },
  failed: { glyph: "✕", tone: "fail", label: "failed" },
  skipped: { glyph: "⊘", tone: "skip", label: "skipped" },
  cancelled: { glyph: "⊗", tone: "skip", label: "cancelled" },
  recovered: { glyph: "⤳", tone: "fallback", label: "recovered" },
};

export const RUN_LOOK: Record<RunStatus, Look> = {
  running: { glyph: "◐", tone: "accent", label: "running" },
  waiting_approval: { glyph: "⏸", tone: "wait", label: "waiting approval" },
  succeeded: { glyph: "●", tone: "ok", label: "succeeded" },
  failed: { glyph: "✕", tone: "fail", label: "failed" },
  rejected: { glyph: "✕", tone: "fail", label: "rejected" },
  needs_attention: { glyph: "⤳", tone: "fallback", label: "needs attention" },
};

export const SPAN_TONE: Record<string, Tone> = {
  succeeded: "ok",
  failed: "fail",
  retrying: "wait",
  waiting: "wait",
  running: "accent",
  interrupted: "wait", // cut short by a crash or shutdown; resume runs another attempt
  cancelled: "skip",
};

const UNKNOWN = (status: string): Look => ({ glyph: "·", tone: "faint", label: status });

export function stepLook(status: string): Look {
  return STEP_LOOK[status as StepStatus] ?? UNKNOWN(status);
}

export function runLook(status: string): Look {
  return RUN_LOOK[status as RunStatus] ?? UNKNOWN(status);
}

/** Every colour is a CSS token defined in index.css. */
export function toneColor(tone: Tone): string {
  return `var(--color-${tone})`;
}
