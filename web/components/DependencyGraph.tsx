"use client";
import { useEffect, useReducer } from "react";
import { ReactFlow, type Node, type Edge, Background, Controls, BackgroundVariant, Panel, useReactFlow, ReactFlowProvider } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { getRepoGraph } from "@/lib/api";
import { Loader2, AlertCircle, Maximize, LayoutGrid } from "lucide-react";
import { Button } from "./ui/button";

/**
 * State for the dependency graph.
 */
type State = { 
  /** List of nodes representing modules. */
  nodes: Node[]; 
  /** List of edges representing dependencies. */
  edges: Edge[]; 
  /** Error message if fetching fails. */
  error: string | null; 
  /** Whether the initial load has completed. */
  loaded: boolean 
};

/**
 * Actions for the dependency graph reducer.
 */
type Action =
  | { type: "reset" }
  | { type: "success"; nodes: Node[]; edges: Edge[] }
  | { type: "error"; message: string };

/**
 * Reducer for managing the dependency graph state.
 */
function reducer(_: State, action: Action): State {
  switch (action.type) {
    case "reset": return { nodes: [], edges: [], error: null, loaded: false };
    case "success": return { nodes: action.nodes, edges: action.edges, error: null, loaded: true };
    case "error": return { nodes: [], edges: [], error: action.message, loaded: true };
  }
}

/**
 * Inner component that needs access to ReactFlow hooks.
 */
function GraphInner({ nodes, edges, loaded, error }: State) {
  const { fitView } = useReactFlow();

  if (error) return (
    <div className="flex flex-col items-center justify-center h-full w-full text-red-500 gap-3 bg-slate-50/50">
      <AlertCircle className="h-10 w-10 opacity-80" />
      <div className="text-center">
        <p className="font-semibold text-slate-900">Failed to load graph</p>
        <p className="text-sm opacity-70">{error}</p>
      </div>
    </div>
  );

  if (!loaded) return (
    <div className="flex flex-col items-center justify-center h-full w-full text-slate-400 gap-3 bg-slate-50/50">
      <Loader2 className="h-10 w-10 animate-spin opacity-50" />
      <p className="text-sm font-medium animate-pulse">Building module map…</p>
    </div>
  );

  if (!nodes.length) return (
    <div className="flex flex-col items-center justify-center h-full w-full text-slate-400 gap-3 bg-slate-50/50">
      <p className="text-sm font-medium">No architectural modules identified yet.</p>
    </div>
  );

  return (
    <div className="w-full h-full bg-white">
      <ReactFlow 
        nodes={nodes} 
        edges={edges} 
        fitView
        fitViewOptions={{ padding: 0.2 }}
        colorMode="light"
        minZoom={0.1}
        maxZoom={2}
        defaultEdgeOptions={{
          type: 'smoothstep',
        }}
      >
        <Background 
          variant={BackgroundVariant.Dots} 
          gap={24} 
          size={1} 
          color="var(--border)" 
        />
        
        <Panel position="top-right" className="flex gap-2">
          <Button 
            variant="outline" 
            size="sm" 
            onClick={() => fitView({ duration: 800, padding: 0.2 })}
            className="bg-white/80 backdrop-blur shadow-sm h-9 px-3 gap-2 border-slate-200"
          >
            <Maximize className="h-4 w-4" />
            <span className="text-xs font-semibold">Fit Canvas</span>
          </Button>
        </Panel>

        <Controls 
          showInteractive={false}
          className="bg-white border border-slate-200 shadow-sm rounded-xl overflow-hidden left-6! bottom-6! flex-col! p-1! gap-1!"
        />

      </ReactFlow>
    </div>
  );
}

/**
 * Visualizes the module dependency graph using ReactFlow.
 */
export default function DependencyGraph({ repoId }: { repoId: string }) {
  const [state, dispatch] = useReducer(reducer, { nodes: [], edges: [], error: null, loaded: false });

  useEffect(() => {
    dispatch({ type: "reset" });
    getRepoGraph(repoId)
      .then((data) => {
        const count = data.nodes.length;
        if (count === 0) {
          dispatch({ type: "success", nodes: [], edges: [] });
          return;
        }
        
        const radius = Math.max(280, count * 50);
        const flowNodes: Node[] = data.nodes.map((n, i) => ({
          id: n.id,
          data: { 
            label: (
              <div className="flex flex-col gap-1.5 py-1">
                <div className="flex items-center justify-center gap-2">
                  <LayoutGrid className="h-3.5 w-3.5 text-indigo-500 opacity-70" />
                  <span className="text-slate-900 font-bold tracking-tight">{n.label}</span>
                </div>
                <div className="h-px w-full bg-slate-100" />
                <span className="text-[9px] text-slate-400 font-bold uppercase tracking-widest">
                  {n.file_count} {n.file_count === 1 ? 'file' : 'files'}
                </span>
              </div>
            )
          },
          position: {
            x: radius * Math.cos((2 * Math.PI * i) / count),
            y: radius * Math.sin((2 * Math.PI * i) / count),
          },
          style: { 
            background: "#ffffff", 
            color: "#0f172a", 
            border: "1px solid #e2e8f0", 
            borderRadius: "14px", 
            padding: "8px 12px",
            fontSize: "13px",
            boxShadow: "0 4px 12px -2px rgb(0 0 0 / 0.08), 0 2px 6px -1px rgb(0 0 0 / 0.04)",
            width: 180,
            textAlign: "center"
          },
        }));

        const flowEdges: Edge[] = data.edges.map((e, i) => ({
          id: `e${i}`,
          source: e.source,
          target: e.target,
          animated: true,
          style: { 
            stroke: "var(--primary, #6366f1)", 
            strokeWidth: 2,
            opacity: 0.4
          },
        }));
        dispatch({ type: "success", nodes: flowNodes, edges: flowEdges });
      })
      .catch((e: Error) => dispatch({ type: "error", message: e.message }));
  }, [repoId]);

  return (
    <ReactFlowProvider>
      <GraphInner {...state} />
    </ReactFlowProvider>
  );
}
