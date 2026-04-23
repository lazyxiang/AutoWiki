import { redirect } from "next/navigation";
import { resolveRouteWiki } from "@/lib/repo-route.server";
import { repoPath } from "@/lib/utils";

export default async function WikiIndex({ params }: { params: Promise<{ owner: string; repo: string }> }) {
  const { owner, repo } = await params;
  const { repoId, repoMeta, pages } = await resolveRouteWiki(owner, repo);
  
  if (pages.length > 0) {
    // Look for an "overview" page or a page with "Overview" in the title
    const overviewPage = pages.find(p => p.slug === "overview" || (p.title && p.title.toLowerCase().includes("overview")));
    const targetPage = overviewPage || pages[0];
    redirect(`${repoPath(repoMeta?.owner ?? owner, repoMeta?.name ?? repo, repoId)}/${targetPage.slug}`);
  }
  
  return <p className="p-8 text-muted-foreground">No wiki pages found.</p>;
}
