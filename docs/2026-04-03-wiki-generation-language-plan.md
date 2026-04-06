# Plan: Wiki Generation Language Feature (EN/ZH)

## Context

AutoWiki currently generates all wiki content in English with no language configuration. The user wants:
1. A language switcher (EN/中文) in the top-right of the main page, defaulting to English
2. Wiki content generated in the selected language
3. The chosen language persisted and displayed on RepoCards

The approach: pass a `wiki_language` parameter from frontend through API → queue → worker pipeline, and append a language instruction to LLM prompts. No full i18n framework needed — the UI stays in English, only the *generated wiki content* changes language.

---

## Step 1: Database — Add `wiki_language` column

**Files:** `shared/models.py`, `shared/database.py`

- Add `wiki_language: Mapped[str | None] = mapped_column(String, nullable=True)` to `Repository` model (after `language` field)
- Add migration in `_apply_migrations()` following the existing pattern:
  ```python
  if "wiki_language" not in columns:
      connection.execute(text("ALTER TABLE repositories ADD COLUMN wiki_language VARCHAR"))
  ```
- All code defaults `None`/missing to `"en"` at the application level

## Step 2: Language instruction helper

**New file:** `worker/pipeline/language.py`

A small module with a dict mapping language codes to prompt suffixes:
- `"en"` → empty string (no extra instruction)
- `"zh"` → instruction telling the LLM to write in 简体中文, keep code identifiers/paths/URLs in English

Two variants:
- `get_language_instruction(lang)` — for page generator (content writing)
- `get_planner_language_instruction(lang)` — for wiki planner (specifies titles/purposes in target language, JSON keys and file paths stay English)

## Step 3: Backend API — Accept and forward `wiki_language`

**File:** `api/routers/repos.py`

- Extend `IndexRequest` with `wiki_language: str = "en"`
- `submit_repo()`: store `wiki_language` on new repos; update it on existing repos during re-submission
- `enqueue_full_index(...)`: pass `wiki_language` through
- `list_repos()` and `get_repo()`: include `wiki_language` in response (default `"en"` for NULL)
- `refresh_repo()`: read stored `wiki_language` from repo row, pass to `enqueue_refresh_index`

**File:** `api/queue.py`

- Add `wiki_language: str = "en"` param to both `enqueue_full_index` and `enqueue_refresh_index`, forward to `_enqueue`

## Step 4: Worker — Thread `wiki_language` through pipeline

**File:** `worker/jobs.py`

- Add `wiki_language: str = "en"` to `run_full_index()` and `run_refresh_index()` signatures
- Pass `wiki_language` to:
  - `generate_wiki_plan(..., wiki_language=wiki_language)` (Stage 5)
  - `generate_page(..., wiki_language=wiki_language)` (Stage 6, in loop)
  - `synthesize_diagrams(..., wiki_language=wiki_language)` (Stage 7)
- When `run_refresh_index` falls back to `run_full_index`, pass `wiki_language` through

## Step 5: Pipeline stages — Inject language instructions into prompts

**File:** `worker/pipeline/wiki_planner.py`
- Add `wiki_language: str = "en"` to `generate_wiki_plan()`
- Build `system = _SYSTEM + get_planner_language_instruction(wiki_language)` and use it in `llm.generate_structured()` call

**File:** `worker/pipeline/page_generator.py`
- Add `wiki_language: str = "en"` to `generate_page()`
- Build `system = _SYSTEM + get_language_instruction(wiki_language)` and use it in the LLM call

**File:** `worker/pipeline/diagram_synthesis.py`
- Add `wiki_language: str = "en"` to `synthesize_diagrams()`
- Keep Mermaid node labels in English (CJK can cause Mermaid rendering issues), but if `wiki_language != "en"`, append a light instruction to use target-language comments

## Step 6: Frontend API client

**File:** `web/lib/api.ts`

- `submitRepo(url, wikiLanguage)`: send `{ url, wiki_language: wikiLanguage }`
- Add `wiki_language: string` to `Repository` and `RepoRaw` interfaces
- Map `wiki_language` in `getRepo()` and `getRepositories()` (default `"en"`)

## Step 7: Frontend — Language switcher + HeroSection

**New file:** `web/components/LanguageSwitcher.tsx`
- A pill-shaped two-segment toggle: "EN" / "中文"
- Active segment: `bg-primary text-primary-foreground`; inactive: `bg-muted text-muted-foreground`
- Uses Globe icon from lucide-react
- Props: `value`, `onChange`

**New file:** `web/components/HeroSection.tsx`
- Client component wrapping the hero section (currently inline in `page.tsx`)
- Manages `wikiLanguage` state (default `"en"`)
- Renders `LanguageSwitcher` in top-right corner (absolute positioned or flex-end)
- Renders `IndexForm` with `wikiLanguage` prop
- Keeps the existing hero text (h1, subtitle)

## Step 8: Frontend — Update existing components

**File:** `web/components/IndexForm.tsx`
- Accept `wikiLanguage?: string` prop
- Pass to `submitRepo(url, wikiLanguage)`

**File:** `web/components/RepoCard.tsx`
- Accept `wikiLanguage?: string` prop
- Display a Globe icon + "中文" / "EN" badge in the metadata row

**File:** `web/app/page.tsx`
- Replace inline hero `<section>` with `<HeroSection />`
- Pass `repo.wiki_language` to each `<RepoCard>`

## Step 9: Tests

- API tests: verify `POST /api/repos` accepts/stores `wiki_language`, responses include it
- Pipeline tests: verify prompts include language instruction when `wiki_language="zh"` and don't when `"en"`
- Migration test: verify `_apply_migrations` adds `wiki_language` column
- Frontend: verify `submitRepo` sends `wiki_language` in request body

---

## Verification

1. Start services: `docker-compose up` or `autowiki serve`
2. Open home page — verify language switcher visible top-right, defaults to "EN"
3. Toggle to "中文", submit a repo URL
4. Monitor job progress — should complete normally
5. View generated wiki — content should be in Chinese with English code terms
6. Check home page RepoCard — should show "中文" badge
7. Refresh the repo — should regenerate in Chinese (inherited language)
8. Run: `pytest tests/ --ignore=tests/e2e` and `npm test --prefix web`
9. Run: `uv run ruff check .` and `npm run lint --prefix web`

## Critical Files

| File | Change |
|------|--------|
| `shared/models.py` | Add `wiki_language` column |
| `shared/database.py` | Add migration for `wiki_language` |
| `worker/pipeline/language.py` | **New** — language instruction helper |
| `api/routers/repos.py` | Accept, store, return, forward `wiki_language` |
| `api/queue.py` | Pass `wiki_language` to ARQ jobs |
| `worker/jobs.py` | Thread `wiki_language` to pipeline stages |
| `worker/pipeline/wiki_planner.py` | Inject language instruction into system prompt |
| `worker/pipeline/page_generator.py` | Inject language instruction into system prompt |
| `worker/pipeline/diagram_synthesis.py` | Light language instruction for diagrams |
| `web/lib/api.ts` | Send/receive `wiki_language` |
| `web/components/LanguageSwitcher.tsx` | **New** — EN/中文 toggle |
| `web/components/HeroSection.tsx` | **New** — client wrapper with language state |
| `web/components/IndexForm.tsx` | Accept and forward `wikiLanguage` |
| `web/components/RepoCard.tsx` | Display wiki language badge |
| `web/app/page.tsx` | Use HeroSection, pass `wiki_language` to RepoCards |
