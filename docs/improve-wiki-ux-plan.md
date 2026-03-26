# Wiki Quality and Navigation Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enhance the wiki generation UX by providing detailed progress descriptions, implementing a hierarchical sidebar with smart "overview" redirection, and fixing Markdown rendering styles.

**Architecture:** 
- Extend the `Job` model with `status_description` and propagate it through the worker and API.
- Update the frontend sidebar to recursively render pages based on `parent_slug`.
- Add a CSS utility layer for Markdown content to restore formatting lost to Tailwind resets.

**Tech Stack:** Python (FastAPI, SQLAlchemy), Next.js (React 19, Tailwind 4), PostgreSQL/SQLite.

---

## Tasks

### Task 1: Add status_description to Job Model

**Files:**
- Modify: `shared/models.py`
- Modify: `shared/database.py` (if needed for migration/init)

- [ ] **Step 1: Add field to `Job` model**
```python
class Job(Base):
    # ... existing fields
    status_description: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 2: Update database initialization if necessary**
The `init_db` function in `shared/database.py` uses `Base.metadata.create_all`, which might not handle adding columns to existing tables in development. Since we are in development, we can just drop and recreate or manually update.

- [ ] **Step 3: Commit**
```bash
git add shared/models.py
git commit -m "feat: add status_description field to Job model"
```

### Task 2: Update Worker to Report Status Descriptions

**Files:**
- Modify: `worker/jobs.py`

- [ ] **Step 1: Update `_update_job` calls in `run_full_index`**
Pass `status_description` to each `_update_job` call.
Examples:
- "Cloning repository and fetching files..."
- "Analyzing source code structure (AST)..."
- "Building dependency graph..."
- "Indexing code for RAG search..."
- "Planning wiki structure..."
- "Generating page: {title}..."

- [ ] **Step 2: Verify worker updates**
Run a local test if possible or mock the DB session.

- [ ] **Step 3: Commit**
```bash
git add worker/jobs.py
git commit -m "feat: report detailed status descriptions during wiki generation"
```

### Task 3: Update API and WebSocket to Expose Status Description

**Files:**
- Modify: `api/routers/jobs.py`
- Modify: `api/ws/jobs.py`

- [ ] **Step 1: Update Job response schemas**
Ensure `status_description` is included in the Pydantic models for Job responses.

- [ ] **Step 2: Update WebSocket message format**
Include `status_description` in the progress update messages.

- [ ] **Step 3: Commit**
```bash
git add api/routers/jobs.py api/ws/jobs.py
git commit -m "feat: expose status_description in API and WebSocket"
```

### Task 4: Display Status Description in Frontend

**Files:**
- Modify: `web/components/JobProgressBar.tsx`

- [ ] **Step 1: Update `useJobProgress` hook or direct state**
Ensure the frontend receives and displays the `status_description`.

- [ ] **Step 2: Update UI layout**
Replace or augment the generic "running..." text with the specific description.

- [ ] **Step 3: Commit**
```bash
git add web/components/JobProgressBar.tsx
git commit -m "feat: display detailed job status in progress bar"
```

### Task 5: Improve Wiki Navigation and Redirection

**Files:**
- Modify: `web/app/[owner]/[repo]/page.tsx`

- [ ] **Step 1: Implement "Overview" redirection logic**
Look for a page with slug `overview` or containing "Overview" in the title. Redirect there if found; otherwise, redirect to the first available page.

- [ ] **Step 2: Commit**
```bash
git add web/app/[owner]/[repo]/page.tsx
git commit -m "fix: redirect to overview page by default"
```

### Task 6: Hierarchical Wiki Sidebar

**Files:**
- Modify: `web/components/WikiSidebar.tsx`

- [ ] **Step 1: Transform flat pages list into a tree structure**
Write a utility to build a tree from the pages list using `parent_slug`.

- [ ] **Step 2: Implement recursive rendering in `WikiSidebar`**
Render nested `<ul>` elements for children. Add indentation or toggle icons.

- [ ] **Step 3: Commit**
```bash
git add web/components/WikiSidebar.tsx
git commit -m "feat: implement hierarchical sidebar navigation"
```

### Task 7: Fix Markdown Rendering Styles

**Files:**
- Modify: `web/app/globals.css`
- Modify: `web/components/WikiPage.tsx`

- [ ] **Step 1: Add `.wiki-content` styles to `globals.css`**
Add styles for `h1`, `h2`, `h3`, `p`, `ul`, `ol`, `li`, `blockquote`, `table`, etc., inside the `.wiki-content` container. Use Tailwind's `@apply` or standard CSS.

```css
.wiki-content {
  @apply leading-relaxed;
}
.wiki-content h1 { @apply text-3xl font-bold mt-8 mb-4 border-b pb-2; }
.wiki-content h2 { @apply text-2xl font-semibold mt-6 mb-3; }
.wiki-content p { @apply my-4; }
.wiki-content ul { @apply list-disc ml-6 my-4; }
/* ... and so on */
```

- [ ] **Step 2: Verify rendering**
Manually check the wiki page display.

- [ ] **Step 3: Commit**
```bash
git add web/app/globals.css
git commit -m "fix: add base styles for Markdown content rendering"
```
