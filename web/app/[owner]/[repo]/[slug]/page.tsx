import { getWikiPage, getRepo, ApiError } from "@/lib/api";
import { WikiPageContent } from "@/components/WikiPage";
import { notFound } from "next/navigation";
import { repoId } from "@/lib/repo-id.server";

export default async function WikiPageRoute({
  params,
}: {
  params: Promise<{ owner: string; repo: string; slug: string }>;
}) {
  const { owner, repo, slug } = await params;
  let rid = owner;

  let page;
  let repository;
  try {
    [page, repository] = await Promise.all([
      getWikiPage(rid, slug),
      getRepo(rid),
    ]);
  } catch (err) {
    const legacyRid = repoId(owner, repo);
    if (legacyRid !== rid) {
      rid = legacyRid;
      try {
        [page, repository] = await Promise.all([
          getWikiPage(rid, slug),
          getRepo(rid),
        ]);
      } catch (legacyErr) {
        if (legacyErr instanceof ApiError && legacyErr.status === 404) {
          notFound();
        }
        throw legacyErr;
      }
    } else if (err instanceof ApiError && err.status === 404) {
      notFound();
    } else {
      throw err;
    }
  }

  if (!page || !repository) {
    notFound();
  }

  return (
    <WikiPageContent 
      content={page.content} 
      owner={repository.owner || owner} 
      repo={repository.name || repo} 
      defaultBranch={repository.default_branch || "main"} 
    />
  );
}
