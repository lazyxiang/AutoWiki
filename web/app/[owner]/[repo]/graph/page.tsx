import crypto from "crypto";
import DependencyGraph from "@/components/DependencyGraph";

export default async function GraphPage({
  params,
}: {
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;
  const repoId = crypto
    .createHash("sha256")
    .update(`github:${owner}/${repo}`)
    .digest("hex")
    .slice(0, 16);

  return (
    <div style={{ padding: "1rem" }}>
      <h1 style={{ marginBottom: "1rem", fontSize: "1.25rem", fontWeight: "bold" }}>
        Module Graph — {owner}/{repo}
      </h1>
      <DependencyGraph repoId={repoId} />
    </div>
  );
}
