import { redirect } from "next/navigation";
import { getRepoWiki } from "@/lib/api";
import crypto from "crypto";

export default async function WikiIndex({ params }: { params: Promise<{ owner: string; repo: string }> }) {
  const { owner, repo } = await params;
  const repoId = crypto.createHash("sha256").update(`github:${owner}/${repo}`).digest("hex").slice(0, 16);
  const { pages } = await getRepoWiki(repoId).catch(() => ({ pages: [] }));
  if (pages.length > 0) redirect(`/${owner}/${repo}/${pages[0].slug}`);
  return <p className="p-8 text-muted-foreground">No wiki pages found.</p>;
}
