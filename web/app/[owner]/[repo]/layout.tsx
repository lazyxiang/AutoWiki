import { WikiSidebar } from "@/components/WikiSidebar";
import { TableOfContents } from "@/components/TableOfContents";
import { ChatDrawer } from "@/components/ChatDrawer";
import { resolveRouteWiki } from "@/lib/repo-route.server";

export default async function WikiLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;
  const { repoId, repoMeta, pages } = await resolveRouteWiki(owner, repo);
  const displayOwner = repoMeta?.owner ?? owner;
  const displayRepo = repoMeta?.name ?? repo;

  return (
    <div className="flex h-screen overflow-hidden">
      <WikiSidebar
        pages={pages}
        owner={displayOwner}
        repo={displayRepo}
        repoId={repoId}
        lastCommit={repoMeta?.last_commit ?? ""}
        indexedAt={repoMeta?.indexed_at ?? ""}
      />
      <main className="flex-1 overflow-y-auto flex justify-center">
        {children}
      </main>
      <TableOfContents />
      <ChatDrawer repoId={repoId} />
    </div>
  );
}
