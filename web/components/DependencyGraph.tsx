"use client";
import { useEffect, useReducer } from "react";
import { ReactFlow, type Node, type Edge, Background, Controls } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { getRepoGraph } from "@/lib/api";

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
          style: { background: "#1f2937", color: "#f9fafb", border: "1px solid #4b5563", borderRadius: "0.5rem", padding: "0.5rem" },
        }));
        const flowEdges: Edge[] = data.edges.map((e, i) => ({
          id: `e${i}`,
          source: e.source,
          target: e.target,
          animated: false,
        }));
        dispatch({ type: "success", nodes: flowNodes, edges: flowEdges });
      })
      .catch((e: Error) => dispatch({ type: "error", message: e.message }));
  }, [repoId]);

  if (error) return <p style={{ color: "#ef4444" }}>Failed to load graph: {error}</p>;
  if (!loaded) return <p style={{ color: "#9ca3af" }}>Loading module graph…</p>;
  if (!nodes.length) return <p style={{ color: "#9ca3af" }}>No modules found.</p>;

  return (
    <div style={{ width: "100%", height: "600px" }}>
      <ReactFlow nodes={nodes} edges={edges} fitView>
        <Background color="#374151" gap={16} />
        <Controls />
      </ReactFlow>
    </div>
  );
}
