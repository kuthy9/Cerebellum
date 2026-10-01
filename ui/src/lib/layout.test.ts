import { describe, expect, it } from "vitest";
import { type Graph, layoutGraph, NODE_W } from "./layout";

const graph: Graph = {
  steps: [
    { id: "a", type: "query", description: "", needs: [], when: null, fallback: null },
    { id: "b", type: "http", description: "", needs: ["a"], when: null, fallback: "rescue" },
    { id: "c", type: "task", description: "", needs: ["a"], when: null, fallback: null },
    { id: "d", type: "task", description: "", needs: ["b", "c"], when: null, fallback: null },
  ],
  fallbacks: [{ id: "rescue", type: "task", description: "", fallback_for: "b" }],
};

const byId = () => Object.fromEntries(layoutGraph(graph).nodes.map((n) => [n.id, n]));

describe("layoutGraph", () => {
  it("places each step one level below its deepest dependency", () => {
    const at = byId();
    expect([at.a.level, at.b.level, at.c.level, at.d.level]).toEqual([0, 1, 1, 2]);
    expect(at.b.y).toBe(at.c.y);
    expect(at.d.y).toBeGreaterThan(at.b.y);
  });

  it("centres each level and keeps siblings apart", () => {
    const at = byId();
    expect(at.a.x + NODE_W / 2).toBe(0);
    expect(at.b.x + at.c.x + NODE_W).toBe(0);
    expect(at.c.x - at.b.x).toBeGreaterThan(NODE_W);
  });

  it("puts fallbacks in their own column, level with the step they rescue", () => {
    const { nodes } = layoutGraph(graph);
    const at = byId();
    const right = Math.max(...nodes.filter((n) => !n.fallback).map((n) => n.x + NODE_W));
    expect(at.rescue.fallback).toBe(true);
    expect(at.rescue.y).toBe(at.b.y);
    expect(at.rescue.x).toBeGreaterThan(right);
  });

  it("draws dependency edges and a fallback edge", () => {
    const { edges } = layoutGraph(graph);
    expect(edges.map((e) => e.id)).toEqual(["a->b", "b~>rescue", "a->c", "b->d", "c->d"]);
    expect(edges.filter((e) => e.fallback).map((e) => e.target)).toEqual(["rescue"]);
  });
});
