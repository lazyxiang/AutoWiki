/**
 * API client for interacting with the AutoWiki backend.
 * Provides functions for submitting repositories, fetching wiki pages,
 * and retrieving repository lists.
 */

// INTERNAL_API_URL is used for server-side SSR calls (Docker: http://api:3001)
// NEXT_PUBLIC_API_URL is baked into the client bundle (browser: http://localhost:3001)
const API_URL =
  typeof window === "undefined"
    ? (process.env.INTERNAL_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:3001")
    : (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:3001");

function settingsApiUrl(path: string): string {
  if (typeof window !== "undefined") {
    return path;
  }
  return `${API_URL}${path}`;
}

function settingsHeaders(extra: HeadersInit = {}): HeadersInit {
  const authToken =
    typeof window === "undefined"
      ? process.env.AUTOWIKI_SERVER_AUTH_TOKEN
      : undefined;
  return authToken
    ? { ...extra, Authorization: `Bearer ${authToken}` }
    : extra;
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
 * Submits a repository URL for indexing.
 *
 * @param url - The GitHub repository URL.
 * @param options.wikiLanguage - Language for wiki generation (default: "en").
 * @param options.reuseIndex - Skip Stage 4 and reuse the existing FAISS index (default: false).
 * @param options.reusePlan - Skip Stage 5 and reuse the existing wiki plan (default: false).
 * @returns A promise resolving to the repository ID, job ID, and status.
 */
export async function submitRepo(
  url: string,
  options: { wikiLanguage?: string; reuseIndex?: boolean; reusePlan?: boolean } = {},
) {
  const { wikiLanguage = "en", reuseIndex = false, reusePlan = false } = options;
  const res = await fetch(`${API_URL}/api/repos`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url,
      wiki_language: wikiLanguage,
      reuse_index: reuseIndex,
      reuse_plan: reusePlan,
    }),
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
  if (!res.ok) throw new ApiError(await res.text(), res.status);
  const repo = await res.json() as RepoRaw;
  const indexedAt = repo.indexed_at || "";
  
  return {
    id: repo.id || "",
    owner: repo.owner || "unknown",
    name: repo.name || "unnamed",
    platform: repo.platform || "github",
    description: repo.description || "",
    stars: repo.stars ?? 0,
    language: repo.language || "Unknown",
    status: repo.status || "unknown",
    default_branch: repo.default_branch || "main",
    indexed_at: indexedAt,
    indexed_at_formatted: repo.indexed_at_formatted || (indexedAt ? new Date(indexedAt).toLocaleString() : "Never"),
    wiki_language: repo.wiki_language || "en",
    last_commit: repo.last_commit || "",
    is_private: repo.is_private ?? false,
    features: repo.features,
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
  if (!res.ok) throw new ApiError(await res.text(), res.status);
  return res.json() as Promise<{ pages: { slug: string; title: string; parent_slug: string | null; has_user_notes?: boolean }[] }>;
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
 * Represents a repository in the AutoWiki system.
 */
export interface Repository {
  id: string;
  owner: string;
  name: string;
  platform: string;
  description: string;
  stars?: number;
  language?: string;
  status: string;
  default_branch?: string;
  indexed_at: string;
  indexed_at_formatted: string;
  wiki_language: string;
  last_commit?: string;
  is_private?: boolean;
  /** Feature flags served by /api/repos/{id}. */
  features?: { deep_research?: boolean };
}

/**
 * Raw repository object returned by the backend API.
 */
interface RepoRaw {
  id?: string;
  owner?: string;
  name?: string;
  platform?: string;
  description?: string;
  stars?: number;
  language?: string;
  status?: string;
  default_branch?: string;
  indexed_at?: string;
  indexed_at_formatted?: string;
  wiki_language?: string;
  last_commit?: string;
  is_private?: boolean;
  features?: { deep_research?: boolean };
}

/**
 * API response for the repository list endpoint.
 */
interface RawReposResponse {
  repos: RepoRaw[];
}

/**
 * Starts a Deep Research job for a repository.
 */
export async function startResearch(
  repoId: string,
  question: string,
): Promise<{ job_id: string; report_id: string; status: string }> {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/research`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export interface ResearchFinding {
  step_index: number;
  answer: string;
  sources: Array<{ file: string; start_line?: number; end_line?: number }>;
}

export interface ResearchPlanStep {
  query: string;
  rationale: string;
}

export interface FastReportCitation {
  id: string;
  file_path: string;
  start_line: number;
  end_line: number;
  label: string;
  kind: string;
  reason?: string;
  score?: number | null;
}

export interface FastReportEvidenceBlock {
  citation_id: string;
  snippet_start: number;
  snippet_end: number;
  full_start: number;
  full_end: number;
  default_context: number;
  expanded_context: number;
  is_collapsed: boolean;
  code: string;
  symbol_path?: string | null;
}

export interface FastReportWikiLink {
  slug: string;
  title: string;
  reason?: string;
}

export interface FastReportDiagram {
  id: string;
  title: string;
  type: string;
  source: string;
  caption?: string;
  reason?: string;
  citations: string[];
  placement: string;
}

export interface FastReportAnalysisTraceFile {
  path: string;
  role: string;
  reason: string;
  status: string;
}

export interface FastReportAnalysisTrace {
  phase?: string;
  language?: string;
  search_plan?: Record<string, unknown>;
  files?: FastReportAnalysisTraceFile[];
}

export interface FastReportSection {
  id: string;
  report_id: string;
  query: string;
  title: string;
  summary: string | null;
  markdown: string;
  citations: FastReportCitation[];
  evidence_blocks: FastReportEvidenceBlock[];
  related_wiki_pages: FastReportWikiLink[];
  related_diagrams: FastReportDiagram[];
  analysis_trace: FastReportAnalysisTrace;
  created_at: string;
  status: string;
}

export interface FastReport {
  id: string;
  repo_id: string;
  job_id: string | null;
  commit_sha: string;
  status: string;
  error: string | null;
  active_section_id: string | null;
  created_at: string;
  expires_at: string;
  sections: FastReportSection[];
}

/**
 * Fetches a completed (or in-progress) Deep Research report.
 */
export async function getResearchReport(
  repoId: string,
  jobId: string,
): Promise<{
  question: string;
  plan: ResearchPlanStep[];
  findings: ResearchFinding[];
  report: string;
  status: string;
  error: string | null;
}> {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/research/${jobId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function startFastReport(
  repoId: string,
  question: string,
): Promise<{
  job_id: string;
  report_id: string;
  section_id: string;
  status: string;
}> {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/fast-reports`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getFastReport(
  repoId: string,
  reportId: string,
): Promise<FastReport> {
  const res = await fetch(`${API_URL}/api/repos/${repoId}/fast-reports/${reportId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
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
  
  return (data.repos || []).map((repo: RepoRaw) => {
    const indexedAt = repo.indexed_at || "";
    return {
      id: repo.id || "",
      owner: repo.owner || "unknown",
      name: repo.name || "unnamed",
      platform: repo.platform || "github",
      description: repo.description || "",
      stars: repo.stars ?? 0,
      language: repo.language || "Unknown",
      status: repo.status || "unknown",
      default_branch: repo.default_branch || "main",
      indexed_at: indexedAt,
      indexed_at_formatted: repo.indexed_at_formatted || (indexedAt ? new Date(indexedAt).toLocaleString() : "Never"),
      wiki_language: repo.wiki_language || "en",
      last_commit: repo.last_commit || "",
      is_private: repo.is_private ?? false,
    };
  });
}

export interface PlatformTokenStatus {
  platform: string;
  has_token: boolean;
  masked_token: string | null;
}

export async function getTokens(): Promise<PlatformTokenStatus[]> {
  const res = await fetch(settingsApiUrl("/api/settings/tokens"), {
    headers: settingsHeaders(),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<PlatformTokenStatus[]>;
}

export async function upsertToken(platform: string, token: string): Promise<void> {
  const res = await fetch(settingsApiUrl(`/api/settings/tokens/${platform}`), {
    method: "PUT",
    headers: settingsHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ token }),
  });
  if (!res.ok) throw new Error(await res.text());
}

export async function deleteToken(platform: string): Promise<void> {
  const res = await fetch(settingsApiUrl(`/api/settings/tokens/${platform}`), {
    method: "DELETE",
    headers: settingsHeaders(),
  });
  if (!res.ok) throw new Error(await res.text());
}
