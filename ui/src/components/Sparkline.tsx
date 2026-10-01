import { sparkPoints } from "../lib/evals";

const PAD = 3;

export function Sparkline({
  values,
  max,
  highlight,
  width = 160,
  height = 32,
  color = "var(--color-accent)",
}: {
  values: (number | null)[];
  max?: number;
  highlight?: number;
  width?: number;
  height?: number;
  color?: string;
}) {
  const points = sparkPoints(values, width - PAD * 2, height - PAD * 2, max).map((p) => ({ ...p, x: p.x + PAD, y: p.y + PAD }));
  return (
    <svg width={width} height={height} className="block shrink-0" aria-hidden="true">
      <line x1={PAD} x2={width - PAD} y1={height - PAD} y2={height - PAD} stroke="var(--color-line)" />
      {points.length > 1 && <polyline points={points.map((p) => `${p.x},${p.y}`).join(" ")} fill="none" stroke={color} strokeWidth={1.25} />}
      {points.map((p) => (
        <circle
          key={p.index}
          cx={p.x}
          cy={p.y}
          r={p.index === highlight ? 2.75 : 1.5}
          fill={p.index === highlight ? color : "var(--color-bg)"}
          stroke={color}
        />
      ))}
    </svg>
  );
}
