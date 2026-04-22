import type { Repository } from "@/lib/api";

export function matchesQuery(query: string, repo: Repository): boolean {
  if (!query.trim()) return true;
  const tokens = query.toLowerCase().split(/\s+/);
  const haystack =
    `${repo.owner} ${repo.name} ${repo.description}`.toLowerCase();
  return tokens.every((t) => haystack.includes(t));
}

export function parseRepoUrl(
  url: string,
): { owner: string; name: string } | null {
  const cleaned = url
    .replace(/^https?:\/\//, "")
    .replace(/\.git$/, "")
    .replace(/\/$/, "");
  const parts = cleaned.split("/");
  // parts[0] = host, parts[1..n-1] = owner segments, parts[n] = name
  if (parts.length < 3) return null;
  const name = parts[parts.length - 1];
  const owner = parts.slice(1, parts.length - 1).join("/");
  if (!name || !owner) return null;
  return { owner, name };
}
