import { getWikiPage, getRepo, ApiError } from "@/lib/api";
import { WikiPageContent } from "@/components/WikiPage";
import { notFound } from "next/navigation";
import { repoId } from "@/lib/utils";

export default async function WikiPageRoute({
  params,
}: {
  params: Promise<{ owner: string; repo: string; slug: string }>;
}) {
  const { owner, repo, slug } = await params;
  const rid = repoId(owner, repo);

  let page;
  let repository;
  try {
    [page, repository] = await Promise.all([
      getWikiPage(rid, slug),
      getRepo(rid),
    ]);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      notFound();
    }
    throw err;
  }

  return (
    <WikiPageContent 
      content={page.content} 
      owner={owner} 
      repo={repo} 
      defaultBranch={repository.default_branch || "main"} 
    />
  );
}
