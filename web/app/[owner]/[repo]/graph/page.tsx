import DependencyGraph from "@/components/DependencyGraph";
import { repoId } from "@/lib/utils";

export default async function GraphPage({
  params,
}: {
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;

  return (
    <div style={{ padding: "1rem" }}>
      <h1 style={{ marginBottom: "1rem", fontSize: "1.25rem", fontWeight: "bold" }}>
        Module Graph — {owner}/{repo}
      </h1>
      <DependencyGraph repoId={repoId(owner, repo)} />
    </div>
  );
}
