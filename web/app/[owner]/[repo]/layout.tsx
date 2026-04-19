import { getRepoWiki, getRepo } from "@/lib/api";
import { WikiSidebar } from "@/components/WikiSidebar";
import { TableOfContents } from "@/components/TableOfContents";
import { ChatDrawer } from "@/components/ChatDrawer";
import { repoId } from "@/lib/utils";

export default async function WikiLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;
  const rid = repoId(owner, repo);
  const [{ pages }, repoMeta] = await Promise.all([
    getRepoWiki(rid).catch(() => ({ pages: [] })),
    getRepo(rid).catch(() => null),
  ]);

  return (
    <div className="flex h-screen overflow-hidden">
      <WikiSidebar
        pages={pages}
        owner={owner}
        repo={repo}
        repoId={rid}
        lastCommit={repoMeta?.last_commit ?? ""}
        indexedAt={repoMeta?.indexed_at ?? ""}
      />
      <main className="flex-1 overflow-y-auto flex justify-center">
        {children}
      </main>
      <TableOfContents />
      <ChatDrawer repoId={rid} />
    </div>
  );
}
