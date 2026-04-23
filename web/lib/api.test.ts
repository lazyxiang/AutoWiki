import { afterEach, describe, expect, it, vi } from "vitest";

async function importApiForServer() {
  vi.resetModules();
  process.env.INTERNAL_API_URL = "http://backend:3001";
  process.env.AUTOWIKI_SERVER_AUTH_TOKEN = "settings-secret";
  return import("./api");
}

async function importApiForBrowser() {
  vi.resetModules();
  vi.stubGlobal("window", {});
  process.env.NEXT_PUBLIC_API_URL = "http://browser-backend:3001";
  return import("./api");
}

describe("settings token API helpers", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.resetModules();
    delete process.env.INTERNAL_API_URL;
    delete process.env.NEXT_PUBLIC_API_URL;
    delete process.env.AUTOWIKI_SERVER_AUTH_TOKEN;
  });

  it("adds the settings bearer token on server-side token reads", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    });
    vi.stubGlobal("fetch", fetchMock);

    const { getTokens } = await importApiForServer();
    await getTokens();

    expect(fetchMock).toHaveBeenCalledWith(
      "http://backend:3001/api/settings/tokens",
      {
        headers: { Authorization: "Bearer settings-secret" },
      },
    );
  });

  it("routes browser-side token writes through the local settings proxy", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => "",
    });
    vi.stubGlobal("fetch", fetchMock);

    const { upsertToken } = await importApiForBrowser();
    await upsertToken("github", "ghp_secret");

    expect(fetchMock).toHaveBeenCalledWith("/api/settings/tokens/github", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: "ghp_secret" }),
    });
  });
});
