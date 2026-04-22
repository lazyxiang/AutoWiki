import { describe, it, expect } from "vitest";
import { matchesQuery, parseRepoUrl } from "./repo-search";
import type { Repository } from "./api";

function makeRepo(owner: string, name: string, description = ""): Repository {
  return {
    id: "test",
    owner,
    name,
    description,
    status: "ready",
    indexed_at: "",
    indexed_at_formatted: "Never",
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
  it("returns null for too-short url", () => {
    expect(parseRepoUrl("github.com/owner")).toBeNull();
  });
  it("returns null for non-url text", () => {
    expect(parseRepoUrl("fastapi")).toBeNull();
  });
});
