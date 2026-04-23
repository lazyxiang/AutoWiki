import "server-only";

import { ApiError, getRepo, getRepoWiki, type Repository } from "@/lib/api";
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
    const repoMeta = await resolveMissingAsNull(() => getRepo(id));
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
      resolveMissingAsNull(() => getRepoWiki(id)).then((wiki) => wiki ?? { pages: [] }),
      resolveMissingAsNull(() => getRepo(id)),
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

async function resolveMissingAsNull<T>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      return null;
    }
    throw err;
  }
}
