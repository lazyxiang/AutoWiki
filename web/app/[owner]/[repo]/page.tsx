import { redirect } from "next/navigation";
import { getRepoWiki } from "@/lib/api";
import { repoId } from "@/lib/utils";

export default async function WikiIndex({ params }: { params: Promise<{ owner: string; repo: string }> }) {
  const { owner, repo } = await params;
  const { pages } = await getRepoWiki(repoId(owner, repo)).catch(() => ({ pages: [] }));
  
  if (pages.length > 0) {
    // Look for an "overview" page or a page with "Overview" in the title
    const overviewPage = pages.find(p => p.slug === "overview" || p.title.toLowerCase().includes("overview"));
    const targetPage = overviewPage || pages[0];
    redirect(`/${owner}/${repo}/${targetPage.slug}`);
  }
  
  return <p className="p-8 text-muted-foreground">No wiki pages found.</p>;
}
