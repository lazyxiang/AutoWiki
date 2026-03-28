import { redirect } from "next/navigation";
import { getRepoWiki } from "@/lib/api";
import { repoId } from "@/lib/utils";

export default async function WikiIndex({ params }: { params: Promise<{ owner: string; repo: string }> }) {
  const { owner, repo } = await params;
  const { pages } = await getRepoWiki(repoId(owner, repo)).catch(() => ({ pages: [] }));
  if (pages.length > 0) {
    redirect(`/${owner}/${repo}/${pages[0].slug}`);
  }
  return <p className="p-8 text-muted-foreground">No wiki pages found.</p>;
}
