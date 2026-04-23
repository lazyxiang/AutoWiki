import "server-only";

const BACKEND_API_URL =
  process.env.INTERNAL_API_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:3001";

export async function proxySettingsRequest(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  const authToken = process.env.AUTOWIKI_SERVER_AUTH_TOKEN;
  if (authToken) {
    headers.set("Authorization", `Bearer ${authToken}`);
  }

  const res = await fetch(`${BACKEND_API_URL}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
  const body = res.status === 204 ? null : await res.arrayBuffer();
  const responseHeaders = new Headers();
  const contentType = res.headers.get("content-type");
  if (contentType) {
    responseHeaders.set("content-type", contentType);
  }
  return new Response(body, {
    status: res.status,
    statusText: res.statusText,
    headers: responseHeaders,
  });
}
