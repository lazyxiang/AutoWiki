import ChatPanel from "@/components/ChatPanel";
import { getRepo } from "@/lib/api";
import { repoId } from "@/lib/repo-id.server";

export default async function ChatPage({
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
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 4rem)", padding: "1rem" }}>
      <h1 style={{ marginBottom: "1rem", fontSize: "1.25rem", fontWeight: "bold" }}>
        Chat — {repoMeta?.owner ?? owner}/{repoMeta?.name ?? repo}
      </h1>
      <ChatPanel repoId={rid} />
    </div>
  );
}
