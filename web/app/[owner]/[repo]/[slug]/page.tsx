import { getWikiPage } from "@/lib/api";
import { WikiPageContent } from "@/components/WikiPage";
import crypto from "crypto";

export default async function WikiPageRoute({
  params,
}: {
  params: Promise<{ owner: string; repo: string; slug: string }>;
}) {
  const { owner, repo, slug } = await params;
  const repoId = crypto.createHash("sha256").update(`github:${owner}/${repo}`).digest("hex").slice(0, 16);
  try {
    const page = await getWikiPage(repoId, slug);
    return <WikiPageContent title={page.title} content={page.content} />;
  } catch {
    return <p className="p-8 text-destructive">Page not found.</p>;
  }
}
