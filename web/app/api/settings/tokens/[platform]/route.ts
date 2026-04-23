import { proxySettingsRequest } from "@/lib/settings-api.server";

type RouteContext = {
  params: Promise<{ platform: string }>;
};

export async function PUT(request: Request, context: RouteContext) {
  const { platform } = await context.params;
  return proxySettingsRequest(`/api/settings/tokens/${encodeURIComponent(platform)}`, {
    method: "PUT",
    headers: {
      "Content-Type": request.headers.get("content-type") ?? "application/json",
    },
    body: await request.text(),
  });
}

export async function DELETE(_request: Request, context: RouteContext) {
  const { platform } = await context.params;
  return proxySettingsRequest(`/api/settings/tokens/${encodeURIComponent(platform)}`, {
    method: "DELETE",
  });
}
