# AutoWiki Phase 5 Design — Multi-Platform Support + Homepage Project Search

**Date:** 2026-04-21  
**Status:** Draft  
**Phases covered:** 5

---

## 1. Scope

Phase 5 redefines the previously-planned roadmap entry ("GitLab/Bitbucket + hybrid search + MCP server") into a focused, shippable set:

| Item | Decision |
|---|---|
| GitLab + Bitbucket support (public + private repos) | ✅ In scope |
| Homepage project search (real-time fuzzy filtering) | ✅ In scope |
| MCP server | ❌ Permanently removed |
| GitHub webhook triggers | ❌ Permanently removed |
| Hybrid search (keyword + semantic) | ⏭ Postponed to Phase 6 |

---

## 2. Architecture Overview

### New files / modules

```text
worker/platform/
  __init__.py
  base.py          ← Platform ABC + RepoMetadata dataclass + custom exceptions
  github.py        ← GitHubPlatform (refactored from ingestion.py)
  gitlab.py        ← GitLabPlatform
  bitbucket.py     ← BitbucketPlatform
  registry.py      ← detect_platform(url) → Platform

shared/models.py   ← + PlatformToken SQLAlchemy model
worker/platform/token_store.py  ← get_platform_token(platform_name, session) helper
api/routers/settings.py         ← token CRUD REST endpoints
web/app/settings/page.tsx       ← settings UI (PAT management)
web/components/HomepageClient.tsx ← client wrapper owning query state
web/components/RepoGrid.tsx       ← filtered + rendered repo card grid
```

### Modified files

```text
worker/pipeline/ingestion.py    ← use platform adapter; remove GitHub-only code
shared/models.py                ← + is_private field on Repository model
api/routers/repos.py            ← URL validation extended to GitLab/Bitbucket
api/main.py                     ← register settings router
web/components/IndexForm.tsx    ← + onQueryChange callback; multi-platform URL parse
web/components/HeroSection.tsx  ← + onQueryChange prop forwarded to IndexForm; settings nav link
web/app/page.tsx                ← pass repos to HomepageClient
web/lib/api.ts                  ← + is_private field on Repository interface
CLAUDE.md                       ← Phase 5/6 roadmap updated
README.md                       ← Phase 5/6 roadmap updated
```

### End-to-end data flows

**Private repo indexing:**
```text
User submits gitlab.com/group/sub/repo
  → detect_platform() → GitLabPlatform
  → look up token from platform_tokens table
  → fetch_metadata(owner, name, token) → GitLab API → RepoMetadata(is_private=True)
  → authenticated_clone_url(owner, name, token) → git clone with embedded token
  → rest of pipeline unchanged (AST, planner, generator)
  → is_private stored on Repository row
```

**Homepage search:**
```text
Server fetches repos → passed as props to HomepageClient
User types "fast" → IndexForm fires onQueryChange("fast")
  → HomepageClient updates query state
  → RepoGrid re-renders: filters repos whose owner/name/description contain "fast"
```

---

## 3. Platform Adapter Layer

### 3.1 Base contract (`worker/platform/base.py`)

```python
@dataclass
class RepoMetadata:
    owner: str
    name: str
    description: str
    stars: int
    language: str          # primary language string, empty string if unknown
    default_branch: str
    is_private: bool

class PrivateRepoError(Exception):
    """Raised when the repo is inaccessible and no token is stored."""

class AuthenticationError(Exception):
    """Raised when a stored token is rejected by the platform."""

class UnsupportedPlatformError(Exception):
    """Raised when the URL hostname is not a recognised platform."""

class Platform(ABC):
    name: str              # "github" | "gitlab" | "bitbucket"

    @abstractmethod
    def parse_url(self, url: str) -> tuple[str, str]:
        """Return (owner, name). Owner may contain slashes for GitLab subgroups."""

    @abstractmethod
    async def fetch_metadata(
        self, owner: str, name: str, token: str | None
    ) -> RepoMetadata:
        """
        Fetch repo metadata from the platform API.
        Raises PrivateRepoError if inaccessible and token is None.
        Raises AuthenticationError if token is present but rejected.
        """

    @abstractmethod
    def authenticated_clone_url(
        self, owner: str, name: str, token: str | None
    ) -> str:
        """Return HTTPS clone URL. Embeds token when provided."""
```

### 3.2 Registry (`worker/platform/registry.py`)

```python
_PLATFORMS: dict[str, Platform] = {
    "github.com":    GitHubPlatform(),
    "gitlab.com":    GitLabPlatform(),
    "bitbucket.org": BitbucketPlatform(),
}

def detect_platform(url: str) -> Platform:
    host = url.replace("https://", "").replace("http://", "").split("/")[0].lower()
    platform = _PLATFORMS.get(host)
    if platform is None:
        raise UnsupportedPlatformError(f"Unsupported host: {host!r}")
    return platform
```

### 3.3 Per-platform implementations

#### GitHubPlatform (`worker/platform/github.py`)

Refactored from existing GitHub-specific code in `ingestion.py`.

- **API base:** `https://api.github.com/repos/{owner}/{name}`
- **Auth header:** `Authorization: Bearer {token}`
- **Privacy field:** `repo["private"]` → `is_private`
- **Language:** `repo["language"]` (string or `null`)
- **Clone URL:** `https://{token}@github.com/{owner}/{name}.git` (token present) or plain HTTPS (no token)
- **Error mapping:** 404 with no token → `PrivateRepoError`; 401/403 with token → `AuthenticationError`

#### GitLabPlatform (`worker/platform/gitlab.py`)

- **API base:** `https://gitlab.com/api/v4/projects/{encoded_path}` where `encoded_path = urllib.parse.quote(f"{owner}/{name}", safe="")`
- **Auth header:** `PRIVATE-TOKEN: {token}`
- **Privacy field:** `repo["visibility"] == "private"` → `is_private` (also `"internal"` counts as non-public)
- **Language:** `GET /api/v4/projects/{encoded_path}/languages` → dict keys ordered by percentage; take the first key
- **Subgroup support:** `parse_url` strips scheme and splits on the last `/`; everything before the last segment is `owner`, last segment is `name`
  - `gitlab.com/group/sub/repo` → `owner="group/sub"`, `name="repo"`
- **Clone URL:** `https://oauth2:{token}@gitlab.com/{owner}/{name}.git`
- **Error mapping:** 404 with no token → `PrivateRepoError`; 401 with token → `AuthenticationError`

#### BitbucketPlatform (`worker/platform/bitbucket.py`)

- **API base:** `https://api.bitbucket.org/2.0/repositories/{owner}/{name}`
- **Auth header:** `Authorization: Bearer {token}`
- **Privacy field:** `repo["is_private"]`
- **Language:** `repo["language"]`
- **Stars:** Bitbucket has no star count; use `0` always
- **Clone URL:** `https://x-token-auth:{token}@bitbucket.org/{owner}/{name}.git`
- **Error mapping:** 401 with no token → `PrivateRepoError`; 403 with token → `AuthenticationError`

---

## 4. Token Storage & Settings

### 4.1 SQLAlchemy model (`shared/models.py`)

```python
class PlatformToken(Base):
    __tablename__ = "platform_tokens"
    platform:    Mapped[str]      = mapped_column(String, primary_key=True)
    token:       Mapped[str]      = mapped_column(String, nullable=False)
    created_at:  Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    updated_at:  Mapped[datetime] = mapped_column(onupdate=lambda: datetime.now(timezone.utc))
```

`platform` is the primary key and takes values `"github"`, `"gitlab"`, `"bitbucket"`. There is at most one token per platform per AutoWiki instance (self-hosted, single-user model).

Tokens are stored as plaintext. Since AutoWiki is self-hosted and the SQLite file is under `~/.autowiki/` (user-owned), database-level encryption is not applied. The GET endpoints never return the raw token.

### 4.2 Token helper (`worker/platform/token_store.py`)

```python
async def get_platform_token(platform_name: str, session: AsyncSession) -> str | None:
    result = await session.get(PlatformToken, platform_name)
    return result.token if result else None
```

Called from `ingestion.py` to retrieve the token before any API or clone operation.

### 4.3 Settings REST API (`api/routers/settings.py`)

```http
GET    /api/settings/tokens
       → [{ platform, has_token, masked_token }]
       masked_token: last 4 chars visible, e.g. "••••••••1234" (null if no token)

PUT    /api/settings/tokens/{platform}
       body: { "token": "ghp_..." }
       → 204 No Content
       Upserts the token; updates updated_at.

DELETE /api/settings/tokens/{platform}
       → 204 No Content
```

`platform` path parameter validated against the set `{"github", "gitlab", "bitbucket"}` — 422 otherwise.

### 4.4 Settings UI (`web/app/settings/page.tsx`)

A new page at `/settings` with three collapsible sections (GitHub, GitLab, Bitbucket), each containing:

- Platform name + logo icon
- Token status: "No token stored" or masked token string
- Password `<input>` for entering/replacing the token
- **Save** button — calls `PUT /api/settings/tokens/{platform}`
- **Clear** button (only shown when a token exists) — calls `DELETE /api/settings/tokens/{platform}`

Navigation to the page: a gear icon button added to the top-right of `HeroSection` (alongside the existing debug toggles).

The settings page is a Server Component for initial load (fetches masked token list), with client-side form handling via `"use client"` child components for each platform section.

---

## 5. Ingestion Pipeline Changes

### 5.1 `worker/pipeline/ingestion.py`

The GitHub-specific metadata fetch and clone URL construction are replaced by adapter calls. The core change inside `ingest_repo()` (or equivalent entry point):

```python
platform = detect_platform(url)
owner, name = platform.parse_url(url)

async with get_session(cfg.database_path) as s:
    token = await get_platform_token(platform.name, s)

# Raises PrivateRepoError or AuthenticationError on failure
metadata = await platform.fetch_metadata(owner, name, token)

clone_url = platform.authenticated_clone_url(owner, name, token)
await clone_or_fetch(clone_path, clone_url)   # already run_in_executor wrapped
```

`PrivateRepoError` and `AuthenticationError` propagate to `run_full_index`, which catches them and stores the message in `job.error` — identical to how existing pipeline errors surface to the frontend.

### 5.2 Repository model (`shared/models.py`)

Add `is_private: Mapped[bool] = mapped_column(Boolean, default=False)` to the `Repository` model. Included in `GET /api/repos` and `GET /api/repos/{id}` response payloads.

### 5.3 URL validation (`api/routers/repos.py`)

The existing GitHub URL regex is replaced by a multi-platform check:

```python
SUPPORTED_HOSTS = {"github.com", "gitlab.com", "bitbucket.org"}

def validate_repo_url(url: str) -> None:
    host = url.replace("https://", "").replace("http://", "").split("/")[0].lower()
    if host not in SUPPORTED_HOSTS:
        raise HTTPException(status_code=422, detail=f"Unsupported host: {host!r}")
```

Minimum path depth (owner + name) is still validated (≥ 2 segments after host).

### 5.4 `web/components/IndexForm.tsx`

The owner/repo redirect after successful submission currently hard-codes the GitHub URL pattern. It is updated to handle all three platforms:

```ts
function parseRepoUrl(url: string): { owner: string; name: string } | null {
  const cleaned = url.replace(/^https?:\/\//, "").replace(/\.git$/, "");
  const parts = cleaned.split("/");
  // parts[0] = host, parts[1..n-1] = owner (may be multi-segment for GitLab), parts[n] = name
  if (parts.length < 3) return null;
  const name = parts[parts.length - 1];
  const owner = parts.slice(1, parts.length - 1).join("/");
  return { owner, name };
}
```

---

## 6. Homepage Project Search

### 6.1 Component architecture

```text
web/app/page.tsx (Server Component)
  → fetches repos via getRepositories()
  → <HomepageClient repos={repos} />

web/components/HomepageClient.tsx ("use client")
  → owns query: string state
  → <HeroSection onQueryChange={setQuery} />
  → <RepoGrid repos={repos} query={query} />

web/components/HeroSection.tsx ("use client", already)
  → receives onQueryChange?: (q: string) => void
  → passes to <IndexForm onQueryChange={onQueryChange} />

web/components/IndexForm.tsx ("use client")
  → receives onQueryChange?: (q: string) => void
  → calls onQueryChange(value) inside the onChange handler

web/components/RepoGrid.tsx ("use client")
  → receives repos: Repository[], query: string
  → filters with matchesQuery(), renders <RepoCard> grid
```

### 6.2 Fuzzy matching (`web/components/RepoGrid.tsx`)

Token-based matching — no new dependencies:

```ts
function matchesQuery(query: string, repo: Repository): boolean {
  if (!query.trim()) return true;
  const tokens = query.toLowerCase().split(/\s+/);
  const haystack =
    `${repo.owner} ${repo.name} ${repo.description ?? ""}`.toLowerCase();
  return tokens.every((t) => haystack.includes(t));
}
```

Each space-separated word in the query must appear somewhere in the combined `owner + name + description` string. Order does not matter. This is "fuzzy enough" for a self-hosted tool with a small corpus, and requires no library.

### 6.3 UI states

| State | Heading | Body |
|---|---|---|
| No query | "Recently Indexed" | Up to 20 repo cards |
| Query, has results | "Results for '{query}'" | Matching cards |
| Query, no results | "Results for '{query}'" | "No repositories match your search." empty state |
| No repos at all | *(heading omitted)* | "No repositories indexed yet. Be the first!" |

### 6.4 Submit button behaviour (unchanged)

The "Get Started" button remains for URL submission only. It is disabled when the input is empty (existing behaviour). Client-side URL detection is not added — the server validates the URL and returns a 422 for non-repo inputs, which surfaces as the existing error message under the form. Real-time filtering and URL submission are entirely orthogonal: the former fires on every keystroke, the latter fires on explicit submit.

---

## 7. API Surface — Full Delta

### New endpoints

```http
GET  /api/settings/tokens
PUT  /api/settings/tokens/{platform}    body: { token: str }
DEL  /api/settings/tokens/{platform}
```

### Modified endpoints

```http
POST /api/repos               — accepts github.com, gitlab.com, bitbucket.org URLs; response includes platform
GET  /api/repos               — response includes platform and is_private fields per repo
GET  /api/repos/{repo_id}     — response includes platform and is_private fields
```

### Unchanged endpoints

All wiki, chat, research, job, and WebSocket endpoints are unaffected.

---

## 8. Roadmap Cleanup

### CLAUDE.md + README.md

Replace:
```text
- **Phase 5** — GitLab/Bitbucket + hybrid search + MCP server
```

With:
```text
- **Phase 5** — GitLab/Bitbucket support (public + private repos, full API metadata) + homepage project search
- **Phase 6** — Hybrid search (keyword + semantic BM25/FAISS fusion)
```

Remove any remaining mentions of MCP server or GitHub webhook triggers from both files.

---

## 9. Testing Strategy

| Test file | Coverage |
|---|---|
| `tests/test_platform_adapters.py` | `parse_url`, `authenticated_clone_url`, and `fetch_metadata` (mocked `httpx`) for all three platforms; subgroup URLs for GitLab; `PrivateRepoError` / `AuthenticationError` paths |
| `tests/test_settings_router.py` | Token upsert, masked GET response, delete, invalid platform 422 |
| `tests/test_ingestion_multiplatform.py` | Mock adapter + token store; verify clone URL passed to `clone_or_fetch`; `PrivateRepoError` surfaces in `job.error` |
| `web/__tests__/RepoGrid.test.tsx` | `matchesQuery` with single token, multi-token, no match, empty query, description match |

Coverage target: ≥ 80% on new `worker/platform/` and `api/routers/settings.py` (consistent with existing project target).

---

## 10. Out of Scope for Phase 5

- Self-hosted GitLab / Bitbucket Server instances (cloud-hosted only)
- Per-user token isolation (single-user self-hosted model; one token per platform globally)
- Token expiry detection or refresh flows
- Highlighting matched text in repo cards
- Pagination of search results
- Re-index / refresh of private repos requires no extra UI — the stored token is used automatically (this is in scope; it is listed here only to note that no UI changes are needed for that path)
