// @vitest-environment jsdom

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { pushMock, pathnameState, paramsState } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  pathnameState: { value: "/openai/autowiki/chat" },
  paramsState: { value: { owner: "openai", repo: "autowiki" } },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
  usePathname: () => pathnameState.value,
  useParams: () => paramsState.value,
}));

import { FloatingAssistant } from "./FloatingAssistant";

describe("FloatingAssistant", () => {
  beforeEach(() => {
    pushMock.mockReset();
    pathnameState.value = "/openai/autowiki/chat";
    paramsState.value = { owner: "openai", repo: "autowiki" };
  });

  it("routes follow-up fast report questions back into the current report workspace", async () => {
    const user = userEvent.setup();

    pathnameState.value = "/openai/autowiki/fast/report-123";
    paramsState.value = {
      owner: "openai",
      repo: "autowiki",
      reportId: "report-123",
    };

    render(React.createElement(FloatingAssistant, { repoId: "repo-1" }));

    await user.type(
      screen.getByLabelText("Ask a question about the codebase"),
      "How does caching change here?",
    );
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(pushMock).toHaveBeenCalledWith(
      "/repo-1/autowiki/fast/report-123?q=How%20does%20caching%20change%20here%3F",
    );
  });
});
