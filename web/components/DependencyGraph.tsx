"use client";
import { useEffect, useReducer } from "react";
import { ReactFlow, type Node, type Edge, Background, Controls, BackgroundVariant } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { getRepoGraph } from "@/lib/api";
import { Loader2, AlertCircle } from "lucide-react";

type State = { nodes: Node[]; edges: Edge[]; error: string | null; loaded: boolean };
type Action =
  | { type: "reset" }
  | { type: "success"; nodes: Node[]; edges: Edge[] }
  | { type: "error"; message: string };

function reducer(_: State, action: Action): State {
  switch (action.type) {
    case "reset": return { nodes: [], edges: [], error: null, loaded: false };
    case "success": return { nodes: action.nodes, edges: action.edges, error: null, loaded: true };
    case "error": return { nodes: [], edges: [], error: action.message, loaded: true };
  }
}

export default function DependencyGraph({ repoId }: { repoId: string }) {
  const [{ nodes, edges, error, loaded }, dispatch] = useReducer(reducer, { nodes: [], edges: [], error: null, loaded: false });

  useEffect(() => {
    dispatch({ type: "reset" });
    getRepoGraph(repoId)
      .then((data) => {
        const count = data.nodes.length;
        if (count === 0) {
          dispatch({ type: "success", nodes: [], edges: [] });
          return;
        }
        const radius = Math.max(200, count * 40);
        const flowNodes: Node[] = data.nodes.map((n, i) => ({
          id: n.id,
          data: { label: `${n.label}\n(${n.file_count} files)` },
          position: {
            x: 400 + radius * Math.cos((2 * Math.PI * i) / count),
            y: 300 + radius * Math.sin((2 * Math.PI * i) / count),
          },
          style: { 
            background: "var(--background, #ffffff)", 
            color: "var(--foreground, #1e293b)", 
            border: "2px solid var(--border, #e2e8f0)", 
            borderRadius: "12px", 
            padding: "10px",
            fontSize: "12px",
            fontWeight: "600",
            boxShadow: "0 4px 6px -1px rgb(0 0 0 / 0.1)",
            width: 150,
            textAlign: "center"
          },
        }));
        const flowEdges: Edge[] = data.edges.map((e, i) => ({
          id: `e${i}`,
          source: e.source,
          target: e.target,
          animated: true,
          style: { stroke: "var(--primary, #6366f1)", strokeWidth: 2 },
        }));
        dispatch({ type: "success", nodes: flowNodes, edges: flowEdges });
      })
      .catch((e: Error) => dispatch({ type: "error", message: e.message }));
  }, [repoId]);

  if (error) return (
    <div className="flex flex-col items-center justify-center h-[600px] text-red-500 gap-2">
      <AlertCircle className="h-8 w-8" />
      <p>Failed to load graph: {error}</p>
    </div>
  );
  if (!loaded) return (
    <div className="flex flex-col items-center justify-center h-[600px] text-slate-400 gap-2">
      <Loader2 className="h-8 w-8 animate-spin" />
      <p>Loading module graph…</p>
    </div>
  );
  if (!nodes.length) return (
    <div className="flex flex-col items-center justify-center h-[600px] text-slate-400 gap-2">
      <p>No modules found.</p>
    </div>
  );

  return (
    <div className="w-full h-[600px] border border-slate-200 rounded-xl overflow-hidden bg-white">
      <ReactFlow 
        nodes={nodes} 
        edges={edges} 
        fitView
        colorMode="light"
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="var(--border, #e2e8f0)" />
        <Controls />
      </ReactFlow>
    </div>
  );
}
