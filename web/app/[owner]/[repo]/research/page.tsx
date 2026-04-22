import ResearchPanel from "@/components/ResearchPanel";
import { getRepo } from "@/lib/api";
import { repoId } from "@/lib/utils";

export default async function ResearchPage({
  params,
}: {
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;
  const rid = repoId(owner, repo);
  const repoMeta = await getRepo(rid).catch(() => null);

  return (
    <div style={{ padding: "1rem" }}>
      <h1
        style={{ fontSize: "1.25rem", fontWeight: "bold", marginBottom: "1rem" }}
      >
        Deep Research — {repoMeta?.owner ?? owner}/{repoMeta?.name ?? repo}
      </h1>
      <ResearchPanel repoId={rid} />
    </div>
  );
}
