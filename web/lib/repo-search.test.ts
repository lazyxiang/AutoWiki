import { describe, it, expect } from "vitest";
import {
  filterRepositoriesForQuery,
  matchesQuery,
  parseRepoUrl,
  sortRepositoriesByIndexedAt,
} from "./repo-search";
import type { Repository } from "./api";

function makeRepo(
  owner: string,
  name: string,
  description = "",
  indexedAt = "",
): Repository {
  return {
    id: "test",
    owner,
    name,
    platform: "github",
    description,
    status: "ready",
    indexed_at: indexedAt,
    indexed_at_formatted: indexedAt || "Never",
    wiki_language: "en",
  };
}

describe("matchesQuery", () => {
  it("returns true for empty query", () => {
    expect(matchesQuery("", makeRepo("owner", "repo"))).toBe(true);
  });
  it("returns true for whitespace-only query", () => {
    expect(matchesQuery("   ", makeRepo("owner", "repo"))).toBe(true);
  });
  it("matches on owner", () => {
    expect(matchesQuery("psf", makeRepo("psf", "requests"))).toBe(true);
  });
  it("matches on name", () => {
    expect(matchesQuery("requests", makeRepo("psf", "requests"))).toBe(true);
  });
  it("matches on description", () => {
    expect(matchesQuery("fast", makeRepo("owner", "repo", "a fast tool"))).toBe(true);
  });
  it("matches multiple space-separated tokens (all must match)", () => {
    expect(matchesQuery("psf requests", makeRepo("psf", "requests"))).toBe(true);
  });
  it("returns false when any token is missing", () => {
    expect(matchesQuery("psf missing", makeRepo("psf", "requests"))).toBe(false);
  });
  it("returns false when nothing matches", () => {
    expect(matchesQuery("xyz", makeRepo("owner", "repo"))).toBe(false);
  });
  it("is case-insensitive", () => {
    expect(matchesQuery("PSF", makeRepo("psf", "requests"))).toBe(true);
  });
});

describe("parseRepoUrl", () => {
  it("parses github url", () => {
    expect(parseRepoUrl("https://github.com/owner/repo")).toEqual({ owner: "owner", name: "repo" });
  });
  it("parses gitlab url with subgroup", () => {
    expect(parseRepoUrl("https://gitlab.com/group/sub/repo")).toEqual({ owner: "group/sub", name: "repo" });
  });
  it("parses bitbucket url", () => {
    expect(parseRepoUrl("https://bitbucket.org/owner/repo")).toEqual({ owner: "owner", name: "repo" });
  });
  it("strips .git suffix", () => {
    expect(parseRepoUrl("https://github.com/owner/repo.git")).toEqual({ owner: "owner", name: "repo" });
  });
  it("strips .git suffix before trailing slash", () => {
    expect(parseRepoUrl("https://gitlab.com/group/repo.git/")).toEqual({ owner: "group", name: "repo" });
  });
  it("parses host URLs without scheme", () => {
    expect(parseRepoUrl("gitlab.com/group/repo")).toEqual({ owner: "group", name: "repo" });
  });
  it("returns null for too-short url", () => {
    expect(parseRepoUrl("github.com/owner")).toBeNull();
  });
  it("returns null for non-url text", () => {
    expect(parseRepoUrl("fastapi")).toBeNull();
  });
  it("returns null for hostless paths", () => {
    expect(parseRepoUrl("foo/bar/baz")).toBeNull();
  });
  it("strips trailing slash", () => {
    expect(parseRepoUrl("https://github.com/owner/repo/")).toEqual({ owner: "owner", name: "repo" });
  });
});

describe("sortRepositoriesByIndexedAt", () => {
  it("sorts repositories by indexed_at descending with missing dates last", () => {
    const old = makeRepo("old", "repo", "", "2024-01-01T00:00:00+00:00");
    const missing = makeRepo("missing", "repo");
    const recent = makeRepo("recent", "repo", "", "2024-02-01T00:00:00+00:00");

    expect(sortRepositoriesByIndexedAt([old, missing, recent])).toEqual([
      recent,
      old,
      missing,
    ]);
  });
});

describe("filterRepositoriesForQuery", () => {
  it("keeps the recent repository list when query looks like a repo URL", () => {
    const recent = makeRepo(
      "recent",
      "repo",
      "",
      "2024-02-01T00:00:00+00:00",
    );
    const old = makeRepo("old", "repo", "", "2024-01-01T00:00:00+00:00");

    expect(
      filterRepositoriesForQuery("https://github.com/unknown/project", [
        old,
        recent,
      ]),
    ).toEqual([recent, old]);
  });

  it("uses fuzzy filtering for non-url search text", () => {
    const requests = makeRepo("psf", "requests", "", "2024-01-01T00:00:00+00:00");
    const django = makeRepo("django", "django", "", "2024-02-01T00:00:00+00:00");

    expect(filterRepositoriesForQuery("psf", [requests, django])).toEqual([
      requests,
    ]);
  });
});
