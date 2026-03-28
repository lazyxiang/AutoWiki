import { getWikiPage, ApiError } from "@/lib/api";
import { WikiPageContent } from "@/components/WikiPage";
import { notFound } from "next/navigation";
import { repoId } from "@/lib/utils";

export default async function WikiPageRoute({
  params,
}: {
  params: Promise<{ owner: string; repo: string; slug: string }>;
}) {
  const { owner, repo, slug } = await params;

  let page;
  try {
    page = await getWikiPage(repoId(owner, repo), slug);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      notFound();
    }
    throw err;
  }

  return <WikiPageContent title={page.title} content={page.content} />;
}
