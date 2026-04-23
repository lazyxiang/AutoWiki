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
  const legacyId = repoId(owner, repo);
  const ids = legacyId === owner ? [owner] : [owner, legacyId];
  let pageData: Awaited<ReturnType<typeof loadPage>> | null = null;

  for (const id of ids) {
    try {
      pageData = await loadPage(id, slug);
      break;
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 404)) {
        throw err;
      }
      if (id === ids.at(-1)) {
        notFound();
      }
    }
  }

  if (!pageData) {
    notFound();
  }

  const [page, repository] = pageData;

  return (
    <WikiPageContent
      content={page.content}
      owner={repository.owner || owner}
      repo={repository.name || repo}
      defaultBranch={repository.default_branch || "main"}
    />
  );
}

function loadPage(repoIdValue: string, slug: string) {
  return Promise.all([getWikiPage(repoIdValue, slug), getRepo(repoIdValue)]);
}
