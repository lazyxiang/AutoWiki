import ResearchPanel from "@/components/ResearchPanel";
import { getRepo } from "@/lib/api";
import { repoId } from "@/lib/repo-id.server";

export default async function ResearchPage({
  params,
}: {
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;
  let rid = owner;
  let repoMeta = await getRepo(rid).catch(() => null);
  if (repoMeta === null) {
    rid = repoId(owner, repo);
    repoMeta = await getRepo(rid).catch(() => null);
  }

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
