import type { Repository } from "@/lib/api";

/** Token-based matching against repository owner, name, and description fields. */
export function matchesQuery(query: string, repo: Repository): boolean {
  const trimmed = query.trim();
  if (!trimmed) return true;
  const tokens = trimmed.toLowerCase().split(/\s+/);
  const haystack =
    `${repo.owner} ${repo.name} ${repo.description}`.toLowerCase();
  return tokens.every((t) => haystack.includes(t));
}

export function isRepositoryUrl(query: string): boolean {
  return parseRepoUrl(query.trim()) !== null;
}

export function sortRepositoriesByIndexedAt(
  repos: Repository[],
): Repository[] {
  return [...repos].sort((a, b) => {
    const aTime = indexedAtTime(a);
    const bTime = indexedAtTime(b);

    if (aTime === null && bTime === null) return 0;
    if (aTime === null) return 1;
    if (bTime === null) return -1;
    return bTime - aTime;
  });
}

export function filterRepositoriesForQuery(
  query: string,
  repos: Repository[],
): Repository[] {
  const sorted = sortRepositoriesByIndexedAt(repos);
  if (isRepositoryUrl(query)) return sorted;
  return sorted.filter((repo) => matchesQuery(query, repo));
}

export function parseRepoUrl(
  url: string,
): { owner: string; name: string } | null {
  const normalized = url.replace(/^gitlab\+(https?:\/\/)/i, "$1");
  const candidate = /^https?:\/\//i.test(normalized)
    ? normalized
    : `https://${normalized}`;
  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    return null;
  }
  if (!parsed.hostname.includes(".")) return null;

  const parts = parsed.pathname
    .replace(/^\/+|\/+$/g, "")
    .split("/")
    .filter(Boolean);
  if (parts.length < 2) return null;

  const rawName = parts[parts.length - 1];
  const name = rawName.endsWith(".git") ? rawName.slice(0, -4) : rawName;
  const owner = parts.slice(0, parts.length - 1).join("/");
  if (!name || !owner) return null;
  return { owner, name };
}

function indexedAtTime(repo: Repository): number | null {
  if (!repo.indexed_at) return null;
  const time = Date.parse(repo.indexed_at);
  return Number.isNaN(time) ? null : time;
}
