import { getRepoWiki } from "@/lib/api";
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
  const { pages } = await getRepoWiki(rid).catch(() => ({ pages: [] }));

  return (
    <div className="flex h-screen overflow-hidden">
      <WikiSidebar pages={pages} owner={owner} repo={repo} repoId={rid} />
      <main className="flex-1 overflow-y-auto flex justify-center">
        {children}
      </main>
      <TableOfContents />
      <ChatDrawer repoId={rid} />
    </div>
  );
}
