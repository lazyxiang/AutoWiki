import ResearchPanel from "@/components/ResearchPanel";
import { resolveRouteRepo } from "@/lib/repo-route.server";

export default async function ResearchPage({
  params,
}: {
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;
  const { repoId, repoMeta } = await resolveRouteRepo(owner, repo);

  return (
    <div style={{ padding: "1rem" }}>
      <h1
        style={{ fontSize: "1.25rem", fontWeight: "bold", marginBottom: "1rem" }}
      >
        Deep Research — {repoMeta?.owner ?? owner}/{repoMeta?.name ?? repo}
      </h1>
      <ResearchPanel repoId={repoId} />
    </div>
  );
}
