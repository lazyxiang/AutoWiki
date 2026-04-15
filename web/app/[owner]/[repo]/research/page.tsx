import ResearchPanel from "@/components/ResearchPanel";
import { repoId } from "@/lib/utils";

export default async function ResearchPage({
  params,
}: {
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;

  return (
    <div style={{ padding: "1rem" }}>
      <h1
        style={{ fontSize: "1.25rem", fontWeight: "bold", marginBottom: "1rem" }}
      >
        Deep Research — {owner}/{repo}
      </h1>
      <ResearchPanel repoId={repoId(owner, repo)} />
    </div>
  );
}
