import "server-only";

import { getRepo, getRepoWiki, type Repository } from "@/lib/api";
import { repoId } from "@/lib/repo-id.server";

type WikiPageSummary = {
  slug: string;
  title: string;
  parent_slug: string | null;
  has_user_notes?: boolean;
};

export async function resolveRouteRepo(owner: string, repo: string) {
  const ids = routeRepoIds(owner, repo);

  for (const id of ids) {
    const repoMeta = await getRepo(id).catch(() => null);
    if (repoMeta) {
      return { repoId: id, repoMeta };
    }
  }

  return { repoId: ids.at(-1) ?? owner, repoMeta: null };
}

export async function resolveRouteWiki(owner: string, repo: string) {
  const ids = routeRepoIds(owner, repo);
  let fallback: {
    repoId: string;
    repoMeta: Repository | null;
    pages: WikiPageSummary[];
  } | null = null;

  for (const id of ids) {
    const [{ pages }, repoMeta] = await Promise.all([
      getRepoWiki(id).catch(() => ({ pages: [] })),
      getRepo(id).catch(() => null),
    ]);
    const result = { repoId: id, repoMeta, pages };
    fallback = result;
    if (repoMeta) {
      return result;
    }
  }

  return fallback ?? { repoId: ids[0], repoMeta: null, pages: [] };
}

function routeRepoIds(owner: string, repo: string) {
  const legacyId = repoId(owner, repo);
  return legacyId === owner ? [owner] : [owner, legacyId];
}
