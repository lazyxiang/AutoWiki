import { redirect } from "next/navigation";
import { getRepoWiki, getRepo } from "@/lib/api";
import { repoId } from "@/lib/repo-id.server";
import { repoPath } from "@/lib/utils";

export default async function WikiIndex({ params }: { params: Promise<{ owner: string; repo: string }> }) {
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
  
  if (pages.length > 0) {
    // Look for an "overview" page or a page with "Overview" in the title
    const overviewPage = pages.find(p => p.slug === "overview" || (p.title && p.title.toLowerCase().includes("overview")));
    const targetPage = overviewPage || pages[0];
    redirect(`${repoPath(repoMeta?.owner ?? owner, repoMeta?.name ?? repo, rid)}/${targetPage.slug}`);
  }
  
  return <p className="p-8 text-muted-foreground">No wiki pages found.</p>;
}
