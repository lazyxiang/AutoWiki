import { describe, expect, it } from "vitest";

import {
  FLOATING_ASSISTANT_HEIGHT_VAR,
  getWorkspaceBottomPadding,
} from "./FastReportWorkspace";
import { sortSectionsForDisplay } from "./ReportStack";

describe("FastReportWorkspace", () => {
  it("reserves bottom space from the floating assistant height variable", () => {
    expect(FLOATING_ASSISTANT_HEIGHT_VAR).toBe("--floating-assistant-height");
    expect(getWorkspaceBottomPadding()).toBe(
      "calc(var(--floating-assistant-height, 7rem) + 2rem)",
    );
  });
});

describe("sortSectionsForDisplay", () => {
  it("shows report sections in creation order", () => {
    expect(
      sortSectionsForDisplay([
        { id: "b", created_at: "2026-04-23T12:00:00Z" },
        { id: "a", created_at: "2026-04-23T09:00:00Z" },
        { id: "c", created_at: "2026-04-23T15:00:00Z" },
      ]),
    ).toEqual(["a", "b", "c"]);
  });
});
