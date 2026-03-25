"use client";
import { useEffect, useState } from "react";
import ReactFlow, { Node, Edge, Background, Controls } from "reactflow";
import "reactflow/dist/style.css";
import { getRepoGraph } from "@/lib/api";

export default function DependencyGraph({ repoId }: { repoId: string }) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getRepoGraph(repoId)
      .then((data) => {
        const count = data.nodes.length;
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
        setNodes(flowNodes);
        setEdges(flowEdges);
      })
      .catch((e) => setError(e.message));
  }, [repoId]);

  if (error) return <p style={{ color: "#ef4444" }}>Failed to load graph: {error}</p>;
  if (!nodes.length) return <p style={{ color: "#9ca3af" }}>Loading module graph…</p>;

  return (
    <div style={{ width: "100%", height: "600px" }}>
      <ReactFlow nodes={nodes} edges={edges} fitView>
        <Background color="#374151" gap={16} />
        <Controls />
      </ReactFlow>
    </div>
  );
}
