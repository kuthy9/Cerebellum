import {
  Background,
  BackgroundVariant,
  Controls,
  type Edge,
  Handle,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
} from "@xyflow/react";
import { type CSSProperties, useMemo } from "react";
import { type Graph, layoutGraph, NODE_H, NODE_W } from "../lib/layout";
import { stepLook, toneColor } from "../lib/status";
import type { Step } from "../types";

type StepNodeData = { label: string; type: string; status: string; attempts: number; fallback: boolean; selected: boolean };
type StepNodeType = Node<StepNodeData, "step">;

const HANDLE: CSSProperties = {
  width: 6,
  height: 6,
  minWidth: 0,
  minHeight: 0,
  border: 0,
  background: "var(--color-line-strong)",
};

function StepNode({ data }: NodeProps<StepNodeType>) {
  const look = stepLook(data.status);
  const dormant = data.fallback && data.status === "pending";
  const busy = data.status === "running" || data.status === "retrying";
  const meta = [data.type, data.fallback ? "fallback" : null, data.attempts > 1 ? `${data.attempts} attempts` : null]
    .filter(Boolean)
    .join(" · ");
  return (
    <div
      className={`flex items-center gap-2.5 border bg-panel px-3 ${data.selected ? "border-accent" : "border-line-strong"} ${
        dormant ? "border-dashed opacity-60" : ""
      }`}
      style={{ width: NODE_W, height: NODE_H }}
    >
      <Handle id="in" type="target" position={Position.Top} style={HANDLE} isConnectable={false} />
      {data.fallback && <Handle id="rescue" type="target" position={Position.Left} style={HANDLE} isConnectable={false} />}
      <span className={`mono text-[14px] ${busy ? "animate-pulse" : ""}`} style={{ color: toneColor(look.tone) }}>
        {look.glyph}
      </span>
      <div className="min-w-0 flex-1">
        <div className="mono truncate text-[12px] text-text">{data.label}</div>
        <div className="truncate text-[10px] uppercase tracking-wider text-faint">{meta}</div>
      </div>
      <Handle id="out" type="source" position={Position.Bottom} style={HANDLE} isConnectable={false} />
      {!data.fallback && <Handle id="fallback" type="source" position={Position.Right} style={HANDLE} isConnectable={false} />}
    </div>
  );
}

const NODE_TYPES = { step: StepNode };

export function RunGraph({
  graph,
  steps,
  selected,
  onSelect,
}: {
  graph: Graph;
  steps?: Record<string, Step>;
  selected?: string | null;
  onSelect?: (stepId: string) => void;
}) {
  const { nodes, edges } = useMemo(() => {
    const laid = layoutGraph(graph);
    const nodes: StepNodeType[] = laid.nodes.map((node) => ({
      id: node.id,
      type: "step",
      position: { x: node.x, y: node.y },
      draggable: false,
      connectable: false,
      data: {
        label: node.id,
        type: node.type,
        status: steps?.[node.id]?.status ?? "pending",
        attempts: steps?.[node.id]?.attempts ?? 0,
        fallback: node.fallback,
        selected: node.id === selected,
      },
    }));
    const edges: Edge[] = laid.edges.map((edge) => {
      const target = steps?.[edge.target]?.status;
      return {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        type: "smoothstep",
        sourceHandle: edge.fallback ? "fallback" : "out",
        targetHandle: edge.fallback ? "rescue" : "in",
        animated: target === "running" || target === "retrying",
        style: edge.fallback
          ? { stroke: "var(--color-fallback)", strokeDasharray: "4 4" }
          : { stroke: "var(--color-line-strong)" },
      };
    });
    return { nodes, edges };
  }, [graph, steps, selected]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      colorMode="dark"
      fitView
      fitViewOptions={{ padding: 0.25 }}
      minZoom={0.3}
      maxZoom={1.4}
      nodesDraggable={false}
      nodesConnectable={false}
      zoomOnScroll={false}
      preventScrolling={false}
      onNodeClick={(_, node) => onSelect?.(node.id)}
    >
      <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}
