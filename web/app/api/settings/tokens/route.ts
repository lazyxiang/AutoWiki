import { proxySettingsRequest } from "@/lib/settings-api.server";

export async function GET() {
  return proxySettingsRequest("/api/settings/tokens");
}
