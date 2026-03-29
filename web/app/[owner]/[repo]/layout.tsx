import { getRepoWiki } from "@/lib/api";
import { WikiSidebar } from "@/components/WikiSidebar";
import { TableOfContents } from "@/components/TableOfContents";
import { repoId } from "@/lib/utils";

export default async function WikiLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;
  const { pages } = await getRepoWiki(repoId(owner, repo)).catch(() => ({ pages: [] }));

  return (
    <div className="flex h-screen overflow-hidden">
      <WikiSidebar pages={pages} owner={owner} repo={repo} />
      <main className="flex-1 overflow-y-auto flex justify-center">
        <div className="w-full max-w-4xl min-w-0">
          {children}
        </div>
      </main>
      <TableOfContents />
    </div>
  );
}
