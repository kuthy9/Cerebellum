export interface GraphStep {
  id: string;
  type: string;
  description: string;
  needs: string[];
  when: string | null;
  fallback: string | null;
}

export interface GraphFallback {
  id: string;
  type: string;
  description: string;
  fallback_for: string | null;
}

export interface Graph {
  steps: GraphStep[];
  fallbacks: GraphFallback[];
}

export interface LaidNode {
  id: string;
  type: string;
  x: number;
  y: number;
  level: number;
  fallback: boolean;
}

export interface LaidEdge {
  id: string;
  source: string;
  target: string;
  fallback: boolean;
}

export const NODE_W = 200;
export const NODE_H = 52;
const GAP_X = 40;
const GAP_Y = 44;

/** Top-to-bottom layered layout: a step sits one level below its deepest dependency, each level is
 * centred on x = 0, and fallbacks get their own column to the right, level with the step they rescue. */
export function layoutGraph(graph: Graph): { nodes: LaidNode[]; edges: LaidEdge[] } {
  const byId = new Map(graph.steps.map((step) => [step.id, step]));
  const levels = new Map<string, number>();
  const depth = (id: string, visiting: Set<string>): number => {
    const known = levels.get(id);
    if (known !== undefined) return known;
    const step = byId.get(id);
    if (!step || visiting.has(id)) return 0; // the loader rejects cycles; never loop here
    visiting.add(id);
    const value = step.needs.length ? 1 + Math.max(...step.needs.map((dep) => depth(dep, visiting))) : 0;
    visiting.delete(id);
    levels.set(id, value);
    return value;
  };

  const rows = new Map<number, GraphStep[]>();
  for (const step of graph.steps) {
    const level = depth(step.id, new Set());
    rows.set(level, [...(rows.get(level) ?? []), step]);
  }

  const nodes: LaidNode[] = [];
  let right = NODE_W / 2;
  for (const [level, steps] of [...rows.entries()].sort((a, b) => a[0] - b[0])) {
    const width = steps.length * NODE_W + (steps.length - 1) * GAP_X;
    steps.forEach((step, index) => {
      const x = -width / 2 + index * (NODE_W + GAP_X);
      right = Math.max(right, x + NODE_W);
      nodes.push({ id: step.id, type: step.type, x, y: level * (NODE_H + GAP_Y), level, fallback: false });
    });
  }

  const taken = new Set<number>();
  for (const fallback of graph.fallbacks) {
    const user = nodes.find((node) => node.id === fallback.fallback_for);
    let y = user ? user.y : 0;
    while (taken.has(y)) y += NODE_H + 12;
    taken.add(y);
    nodes.push({ id: fallback.id, type: fallback.type, x: right + GAP_X * 2, y, level: user?.level ?? 0, fallback: true });
  }

  const edges: LaidEdge[] = [];
  for (const step of graph.steps) {
    for (const dep of step.needs) edges.push({ id: `${dep}->${step.id}`, source: dep, target: step.id, fallback: false });
    if (step.fallback) {
      edges.push({ id: `${step.id}~>${step.fallback}`, source: step.id, target: step.fallback, fallback: true });
    }
  }
  return { nodes, edges };
}
