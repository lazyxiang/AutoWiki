import { getRepoWiki, getRepo } from "@/lib/api";
import { WikiSidebar } from "@/components/WikiSidebar";
import { TableOfContents } from "@/components/TableOfContents";
import { ChatDrawer } from "@/components/ChatDrawer";
import { repoId } from "@/lib/repo-id.server";

export default async function WikiLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;
  let rid = owner;
  let [{ pages }, repoMeta] = await Promise.all([
    getRepoWiki(rid).catch(() => ({ pages: [] })),
    getRepo(rid).catch(() => null),
  ]);
  if (repoMeta === null) {
    rid = repoId(owner, repo);
    [{ pages }, repoMeta] = await Promise.all([
      getRepoWiki(rid).catch(() => ({ pages: [] })),
      getRepo(rid).catch(() => null),
    ]);
  }
  const displayOwner = repoMeta?.owner ?? owner;
  const displayRepo = repoMeta?.name ?? repo;

  return (
    <div className="flex h-screen overflow-hidden">
      <WikiSidebar
        pages={pages}
        owner={displayOwner}
        repo={displayRepo}
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
