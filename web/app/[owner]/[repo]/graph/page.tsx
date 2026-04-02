import DependencyGraph from "@/components/DependencyGraph";
import { repoId } from "@/lib/utils";

export default async function GraphPage({
  params,
}: {
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;

  return (
    <div className="flex flex-col h-full w-full p-6 overflow-hidden">
      <div className="flex items-center justify-between mb-6 shrink-0">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 leading-none">Module Graph</h1>
          <p className="text-sm text-slate-500 mt-2 font-mono">{owner}/{repo}</p>
        </div>
      </div>
      
      <div className="flex-1 min-h-0 relative bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden">
        <DependencyGraph repoId={repoId(owner, repo)} />
      </div>
    </div>
  );
}
