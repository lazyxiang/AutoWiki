import { getWikiPage, ApiError } from "@/lib/api";
import { WikiPageContent } from "@/components/WikiPage";
import { notFound } from "next/navigation";
import crypto from "crypto";

export default async function WikiPageRoute({
  params,
}: {
  params: Promise<{ owner: string; repo: string; slug: string }>;
}) {
  const { owner, repo, slug } = await params;
  const repoId = crypto
    .createHash("sha256")
    .update(`github:${owner}/${repo}`)
    .digest("hex")
    .slice(0, 16);

  let page;
  try {
    page = await getWikiPage(repoId, slug);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      notFound();
    }
    throw err;
  }

  return <WikiPageContent title={page.title} content={page.content} />;
}
