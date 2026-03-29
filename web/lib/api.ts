// INTERNAL_API_URL is used for server-side SSR calls (Docker: http://api:3001)
// NEXT_PUBLIC_API_URL is baked into the client bundle (browser: http://localhost:3001)
const API_URL =
  typeof window === "undefined"
    ? (process.env.INTERNAL_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:3001")
    : (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:3001");

export async function submitRepo(url: string) {
  const res = await fetch(`${API_URL}/api/repos`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ repo_id: string; job_id: string; status: string }>;
}

export async function getJob(jobId: string) {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ status: string; progress: number; error?: string }>;
}

export async function getRepoWiki(repoId: string) {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/wiki`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ pages: { slug: string; title: string; parent_slug: string | null }[] }>;
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function getWikiPage(repoId: string, slug: string) {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/wiki/${slug}`);
  if (!res.ok) {
    throw new ApiError(await res.text(), res.status);
  }
  return res.json() as Promise<{ slug: string; title: string; content: string }>;
}

export async function createChatSession(repoId: string): Promise<{ session_id: string }> {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/chat`, { method: "POST" });
  if (!res.ok) throw new Error(`Failed to create chat session: ${res.status}`);
  return res.json();
}

export async function getChatHistory(repoId: string, sessionId: string): Promise<{
  messages: Array<{ role: string; content: string }>;
}> {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/chat/${sessionId}`);
  if (!res.ok) throw new Error(`Failed to get chat history: ${res.status}`);
  return res.json();
}

export async function refreshRepo(repoId: string): Promise<{ repo_id: string; job_id: string; status: string }> {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/refresh`, { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getRepoGraph(repoId: string): Promise<{
  nodes: Array<{ id: string; label: string; file_count: number }>;
  edges: Array<{ source: string; target: string }>;
}> {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/graph`);
  if (!res.ok) throw new Error(`Failed to get graph: ${res.status}`);
  return res.json();
}

export interface Repository {
  id: string;
  owner: string;
  name: string;
  description: string;
  stars?: number;
  language?: string;
  status: string;
  indexed_at: string;
  indexed_at_formatted: string;
}

export async function getRepositories(): Promise<Repository[]> {
  const res = await fetch(`${API_URL}/api/repos`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
