import { describe, expect, it, vi } from "vitest";
import { repoId } from "./repo-id.server";
import { repoPath } from "./utils";

vi.mock("server-only", () => ({}));

describe("repoId", () => {
  it("includes platform in the stable repository hash", () => {
    expect(repoId("owner", "repo", "github")).not.toEqual(
      repoId("owner", "repo", "gitlab"),
    );
  });

  it("defaults to GitHub for legacy routes", () => {
    expect(repoId("owner", "repo")).toEqual(repoId("owner", "repo", "github"));
  });

  it("does not treat a 16-hex owner as a repo id unless explicit", () => {
    expect(repoId("deadbeefdeadbeef", "repo-a")).not.toEqual(
      repoId("deadbeefdeadbeef", "repo-b"),
    );
    expect(
      repoId("deadbeefdeadbeef", "repo-a", "github", { ownerIsRepoId: true }),
    ).toEqual("deadbeefdeadbeef");
  });
});

describe("repoPath", () => {
  it("routes by repository id while retaining a readable repo segment", () => {
    expect(repoPath("group/sub", "repo", "abc123")).toEqual("/abc123/repo");
  });
});
