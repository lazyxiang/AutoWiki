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
  const candidate = /^https?:\/\//i.test(url) ? url : `https://${url}`;
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
