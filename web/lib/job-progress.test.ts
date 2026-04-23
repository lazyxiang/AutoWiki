import { describe, expect, it } from "vitest";

import { parseJobProgressDetail } from "./job-progress";

describe("parseJobProgressDetail", () => {
  it("parses the current generated page detail", () => {
    expect(
      parseJobProgressDetail('Generating page "API Layer" (3/8) [level 2/4]'),
    ).toEqual({
      kind: "page",
      title: "API Layer",
    });
  });

  it("parses active batch page details while pages are being generated", () => {
    expect(
      parseJobProgressDetail(
        'Generating pages 5-7/12 [level 2/3]: "API Layer", "Worker Pipeline", "CLI"',
      ),
    ).toEqual({
      kind: "batch",
      titles: ["API Layer", "Worker Pipeline", "CLI"],
    });
  });

  it("parses active page stages without queued batch metadata", () => {
    expect(
      parseJobProgressDetail(
        'Generating active pages: "API Layer" [Draft], "Worker Pipeline" [Fact-check]',
      ),
    ).toEqual({
      kind: "active",
      pages: [
        { title: "API Layer", stage: "Draft" },
        { title: "Worker Pipeline", stage: "Fact-check" },
      ],
    });
  });
});
