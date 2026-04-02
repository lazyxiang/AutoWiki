/**
 * API client for interacting with the AutoWiki backend.
 * Provides functions for submitting repositories, fetching wiki pages,
 * managing chat sessions, and retrieving repository lists.
 */

// INTERNAL_API_URL is used for server-side SSR calls (Docker: http://api:3001)
// NEXT_PUBLIC_API_URL is baked into the client bundle (browser: http://localhost:3001)
const API_URL =
  typeof window === "undefined"
    ? (process.env.INTERNAL_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:3001")
    : (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:3001");

/**
 * Submits a repository URL for indexing.
 * 
 * @param url - The GitHub repository URL.
 * @returns A promise resolving to the repository ID, job ID, and status.
 */
export async function submitRepo(url: string) {
  const res = await fetch(`${API_URL}/api/repos`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ repo_id: string; job_id: string; status: string }>;
}

/**
 * Fetches the status and progress of a background job.
 * 
 * @param jobId - The UUID of the job.
 * @returns Job status, progress (0-100), and optional error message.
 */
export async function getJob(jobId: string) {
  const res = await fetch(`${API_URL}/api/jobs/${jobId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ status: string; progress: number; error?: string }>;
}

/**
 * Fetches the metadata for a single repository.
 * 
 * @param repoId - The repository ID.
 * @returns Repository metadata.
 */
export async function getRepo(repoId: string): Promise<Repository> {
  const res = await fetch(`${API_URL}/api/repos/${repoId}`);
  if (!res.ok) throw new Error(await res.text());
  const repo = await res.json();
  return {
    id: repo.id || "",
    owner: repo.owner || "unknown",
    name: repo.name || "unnamed",
    description: repo.description || "",
    stars: repo.stars ?? 0,
    language: repo.language || "Unknown",
    status: repo.status || "unknown",
    indexed_at: repo.indexed_at || "",
    indexed_at_formatted: repo.indexed_at_formatted || "Never",
  };
}
/**
 * Retrieves the hierarchical wiki structure for a repository.
 * 
 * @param repoId - The SHA256 hash of the repository URL.
 * @returns A list of wiki pages with slugs and titles.
 */
export async function getRepoWiki(repoId: string) {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/wiki`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ pages: { slug: string; title: string; parent_slug: string | null }[] }>;
}

/**
 * Custom error class for API failures containing the HTTP status code.
 */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

/**
 * Fetches the Markdown content of a specific wiki page.
 * 
 * @param repoId - The repository ID.
 * @param slug - The URL-friendly slug of the page.
 * @returns The page title and Markdown content.
 */
export async function getWikiPage(repoId: string, slug: string) {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/wiki/${slug}`);
  if (!res.ok) {
    throw new ApiError(await res.text(), res.status);
  }
  return res.json() as Promise<{ slug: string; title: string; content: string }>;
}

/**
 * Creates a new chat session for a repository.
 * 
 * @param repoId - The repository ID.
 * @returns The session ID.
 */
export async function createChatSession(repoId: string): Promise<{ session_id: string }> {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/chat`, { method: "POST" });
  if (!res.ok) throw new Error(`Failed to create chat session: ${res.status}`);
  return res.json();
}

/**
 * Retrieves the message history for a chat session.
 * 
 * @param repoId - The repository ID.
 * @param sessionId - The chat session ID.
 * @returns A list of messages with roles and content.
 */
export async function getChatHistory(repoId: string, sessionId: string): Promise<{
  messages: Array<{ role: string; content: string }>;
}> {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/chat/${sessionId}`);
  if (!res.ok) throw new Error(`Failed to get chat history: ${res.status}`);
  return res.json();
}

/**
 * Triggers a manual refresh/re-index of a repository.
 * 
 * @param repoId - The repository ID.
 * @returns The new job ID and status.
 */
export async function refreshRepo(repoId: string): Promise<{ repo_id: string; job_id: string; status: string }> {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/refresh`, { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/**
 * Fetches the module dependency graph for a repository.
 * 
 * @param repoId - The repository ID.
 * @returns Nodes and edges for visualization.
 */
export async function getRepoGraph(repoId: string): Promise<{
  nodes: Array<{ id: string; label: string; file_count: number }>;
  edges: Array<{ source: string; target: string }>;
}> {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/graph`);
  if (!res.ok) throw new Error(`Failed to get graph: ${res.status}`);
  return res.json();
}

/**
 * Represents a repository in the AutoWiki system.
 */
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

/**
 * Raw repository object returned by the backend API.
 */
interface RepoRaw {
  id?: string;
  owner?: string;
  name?: string;
  description?: string;
  stars?: number;
  language?: string;
  status?: string;
  indexed_at?: string;
  indexed_at_formatted?: string;
}

/**
 * API response for the repository list endpoint.
 */
interface RawReposResponse {
  repos: RepoRaw[];
}

/**
 * Fetches a list of all indexed repositories.
 * 
 * @returns A promise resolving to an array of Repository objects.
 */
export async function getRepositories(): Promise<Repository[]> {
  const res = await fetch(`${API_URL}/api/repos`);
  if (!res.ok) throw new Error(await res.text());
  const data = await res.json() as RawReposResponse;
  
  return (data.repos || []).map((repo: RepoRaw) => ({
    id: repo.id || "",
    owner: repo.owner || "unknown",
    name: repo.name || "unnamed",
    description: repo.description || "",
    stars: repo.stars ?? 0,
    language: repo.language || "Unknown",
    status: repo.status || "unknown",
    indexed_at: repo.indexed_at || "",
    indexed_at_formatted: repo.indexed_at_formatted || "Never",
  }));
}
