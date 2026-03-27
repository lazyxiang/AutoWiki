import ChatPanel from "@/components/ChatPanel";
import crypto from "crypto";

export default async function ChatPage({
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
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 4rem)", padding: "1rem" }}>
      <h1 style={{ marginBottom: "1rem", fontSize: "1.25rem", fontWeight: "bold" }}>
        Chat — {owner}/{repo}
      </h1>
      <ChatPanel repoId={repoId} />
    </div>
  );
}
