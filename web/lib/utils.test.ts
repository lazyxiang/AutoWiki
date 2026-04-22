import { describe, expect, it } from "vitest";
import { repoId, repoPath } from "./utils";

describe("repoId", () => {
  it("includes platform in the stable repository hash", () => {
    expect(repoId("owner", "repo", "github")).not.toEqual(
      repoId("owner", "repo", "gitlab"),
    );
  });

  it("defaults to GitHub for legacy routes", () => {
    expect(repoId("owner", "repo")).toEqual(repoId("owner", "repo", "github"));
  });
});

describe("repoPath", () => {
  it("routes by repository id while retaining a readable repo segment", () => {
    expect(repoPath("group/sub", "repo", "abc123")).toEqual("/abc123/repo");
  });
});
