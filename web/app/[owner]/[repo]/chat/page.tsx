import ChatPanel from "@/components/ChatPanel";
import { resolveRouteRepo } from "@/lib/repo-route.server";

export default async function ChatPage({
  params,
}: {
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;
  const { repoId, repoMeta } = await resolveRouteRepo(owner, repo);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 4rem)", padding: "1rem" }}>
      <h1 style={{ marginBottom: "1rem", fontSize: "1.25rem", fontWeight: "bold" }}>
        Chat — {repoMeta?.owner ?? owner}/{repoMeta?.name ?? repo}
      </h1>
      <ChatPanel repoId={repoId} />
    </div>
  );
}
