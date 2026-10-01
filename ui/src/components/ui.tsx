import type { ButtonHTMLAttributes, ReactNode } from "react";
import { fmtJson } from "../lib/format";
import { runLook, stepLook, toneColor } from "../lib/status";

export function StatusChip({ status, kind = "run" }: { status: string; kind?: "run" | "step" }) {
  const look = kind === "run" ? runLook(status) : stepLook(status);
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap" style={{ color: toneColor(look.tone) }}>
      <span className="mono text-[11px]">{look.glyph}</span>
      <span>{look.label}</span>
    </span>
  );
}

export function Label({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`label ${className}`}>{children}</div>;
}

export function Panel({
  title,
  actions,
  children,
  className = "",
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`border border-line bg-panel ${className}`}>
      {title !== undefined && (
        <header className="flex h-9 items-center justify-between border-b border-line px-3">
          <Label>{title}</Label>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="flex h-14 items-center justify-between gap-4 border-b border-line px-6">
      <div className="flex min-w-0 items-baseline gap-3">
        <h1 className="truncate text-[15px] font-medium text-text">{title}</h1>
        {subtitle && <span className="truncate text-[12px] text-faint">{subtitle}</span>}
      </div>
      {actions}
    </header>
  );
}

export function JsonBlock({ value, className = "" }: { value: unknown; className?: string }) {
  return (
    <pre className={`mono overflow-auto whitespace-pre-wrap break-words text-[11.5px] leading-relaxed text-muted ${className}`}>
      {fmtJson(value)}
    </pre>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="px-4 py-10 text-center text-[12px] text-faint">{children}</div>;
}

type ButtonTone = "default" | "accent" | "ok" | "fail";

const BUTTON_TONES: Record<ButtonTone, string> = {
  default: "border-line-strong text-text hover:border-muted",
  accent: "border-accent/60 text-accent hover:bg-accent/10",
  ok: "border-ok/60 text-ok hover:bg-ok/10",
  fail: "border-fail/60 text-fail hover:bg-fail/10",
};

export function Button({
  tone = "default",
  className = "",
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: ButtonTone }) {
  return (
    <button
      type={type}
      {...props}
      className={`h-8 border px-3 text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${BUTTON_TONES[tone]} ${className}`}
    />
  );
}

export function Tabs<T extends string>({ value, options, onChange }: { value: T; options: readonly T[]; onChange: (value: T) => void }) {
  return (
    <div className="flex border border-line">
      {options.map((option) => (
        <button
          key={option}
          type="button"
          onClick={() => onChange(option)}
          className={`h-7 px-3 text-[11.5px] ${option === value ? "bg-raised text-text" : "text-faint hover:text-muted"}`}
        >
          {option}
        </button>
      ))}
    </div>
  );
}

export function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <span className="label">{label}</span>
      <span className="mono text-[12px] text-muted">{children}</span>
    </span>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="text-[12px] text-fail">{error instanceof Error ? error.message : String(error)}</div>;
}
