"""ARQ job functions that orchestrate the 7-stage wiki generation pipeline.

Registered in ``worker/main.py`` as background tasks executed by the ARQ
worker.  Two entry points are exposed:

- ``run_full_index``: Complete pipeline from scratch — clears all previous
  artifacts and runs all 7 stages for a repository.
- ``run_refresh_index``: Incremental refresh — re-runs only the stages and
  pages that are affected by commits since the last index, with several
  fallback paths that escalate to a full reindex when necessary.

Helper functions in this module are prefixed with ``_`` and are shared
between the two job entry points.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import delete as sa_delete
from sqlalchemy import select as sa_select

from shared.config import get_config
from shared.database import get_session, init_db
from shared.models import Job, Repository, WikiPage
from worker.embedding import make_embedding_provider
from worker.llm import make_llm_provider
from worker.pipeline.ast_analysis import FileAnalysis, analyze_all_files
from worker.pipeline.dependency_graph import build_dependency_graph
from worker.pipeline.diagram_synthesis import synthesize_diagrams
from worker.pipeline.ingestion import (
    clone_or_fetch,
    extract_readme,
    fetch_github_metadata,
    filter_files,
    get_affected_pages,
    get_changed_files,
)
from worker.pipeline.page_generator import generate_page
from worker.pipeline.rag_indexer import FAISSStore, build_rag_index
from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan, generate_wiki_plan

if TYPE_CHECKING:
    from worker.pipeline.dependency_graph import DependencyGraph

logger = logging.getLogger("worker.task")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _update_job(db_path: str, job_id: str, **kwargs) -> None:
    """Update one or more columns on a ``Job`` row in the database.

    Fetches the row by primary key and applies arbitrary column updates via
    ``setattr``, so callers can pass any valid ``Job`` model column name as
    a keyword argument.

    Args:
        db_path (str): Filesystem path to the SQLite database file.
        job_id (str): UUID primary key of the job row to update.
        **kwargs: Keyword arguments whose names are ``Job`` model column
            names (e.g. ``status``, ``progress``, ``error``,
            ``status_description``, ``finished_at``).

    Returns:
        None
    """
    async with get_session(db_path) as s:
        job = await s.get(Job, job_id)
        for k, v in kwargs.items():
            setattr(job, k, v)
        await s.commit()


async def _update_repo(db_path: str, repo_id: str, **kwargs) -> None:
    """Update one or more columns on a ``Repository`` row in the database.

    Fetches the row by primary key and applies arbitrary column updates via
    ``setattr``.

    Args:
        db_path (str): Filesystem path to the SQLite database file.
        repo_id (str): UUID primary key of the repository row to update.
        **kwargs: Keyword arguments whose names are ``Repository`` model
            column names (e.g. ``status``, ``last_commit``, ``indexed_at``,
            ``wiki_path``, ``wiki_structure``).

    Returns:
        None
    """
    async with get_session(db_path) as s:
        repo = await s.get(Repository, repo_id)
        for k, v in kwargs.items():
            setattr(repo, k, v)
        await s.commit()


async def _write_text_async(path: Path, content: str) -> None:
    """Write a string to a file without blocking the event loop.

    Delegates to ``Path.write_text`` via ``loop.run_in_executor`` so that
    disk I/O does not stall the async event loop while other coroutines are
    waiting.

    Args:
        path (Path): Destination file path.  The parent directory must
            already exist.
        content (str): Text content to write (UTF-8 by default, as per
            ``Path.write_text``).

    Returns:
        None
    """
    loop = asyncio.get_running_loop()
    # run_in_executor offloads the blocking write to the default thread pool
    await loop.run_in_executor(None, path.write_text, content)


# ---------------------------------------------------------------------------
# Pipeline stage helpers (shared by full-index and refresh)
# ---------------------------------------------------------------------------


def _make_on_retry(db_path: str, job_id: str):
    """Return an ``on_retry`` callback that writes retry status to the DB.

    The returned coroutine is passed to pipeline helpers that accept an
    ``on_retry`` argument (e.g. ``build_rag_index``, ``generate_wiki_plan``,
    ``generate_page``).  It is called by ``async_retry`` before each
    retry attempt so the frontend can display live retry information.

    Args:
        db_path (str): Filesystem path to the SQLite database file.
        job_id (str): UUID of the job row to update on each retry.

    Returns:
        Callable: An async callback with the signature::

            async def _on_retry(
                attempt: int,
                max_retries: int,
                wait: float,
                exc: Exception,
            ) -> None: ...

        Where ``attempt`` is the 1-based retry count, ``max_retries`` is
        the configured retry ceiling, ``wait`` is the back-off delay in
        seconds, and ``exc`` is the exception that triggered the retry.
    """

    async def _on_retry(
        attempt: int, max_retries: int, wait: float, exc: Exception
    ) -> None:
        await _update_job(
            db_path,
            job_id,
            status_description=(
                f"Retry {attempt}/{max_retries} in {wait:.0f}s ({type(exc).__name__})"
            ),
        )

    return _on_retry


def _collect_page_entities(
    page_spec: WikiPageSpec, file_analysis: FileAnalysis
) -> list[dict]:
    """Collect all AST entities for the source files assigned to a wiki page.

    Iterates over each file path listed in ``page_spec.files``, looks up
    its ``FileInfo`` in ``file_analysis``, and flattens all entities into a
    single list.  A ``"file"`` key is injected into each entity dict so
    downstream consumers can attribute entities to their source file.

    Args:
        page_spec (WikiPageSpec): Wiki page specification containing the
            list of relative file paths (``page_spec.files``) assigned to
            this page.
        file_analysis (FileAnalysis): Result of single-pass Tree-Sitter
            analysis across all repository files, keyed by relative path.

    Returns:
        list[dict]: Flat list of entity dicts.  Each dict contains the
            original entity fields (e.g. ``"name"``, ``"kind"``,
            ``"signature"``) plus an added ``"file"`` key (str) holding
            the relative path of the source file.

    Example:
        >>> entities = _collect_page_entities(page_spec, file_analysis)
        >>> entities[0]
        {"name": "run_full_index", "kind": "function", "file": "worker/jobs.py"}
    """
    entities = []
    for rel_path in page_spec.files or []:
        file_info = file_analysis.files.get(rel_path)
        if file_info:
            for e in file_info.entities:
                entities.append({**e, "file": rel_path})
    return entities


def _collect_page_deps(page_spec: WikiPageSpec, dep_graph: DependencyGraph) -> dict:
    """Collect dependency summary for the source files assigned to a wiki page.

    Delegates to ``summarize_page_deps`` which aggregates import-level edges
    from the dependency graph into human-readable buckets.

    Args:
        page_spec (WikiPageSpec): Wiki page specification containing the
            list of relative file paths (``page_spec.files``) assigned to
            this page.
        dep_graph (DependencyGraph): File-level import graph built by
            ``build_dependency_graph``.

    Returns:
        dict: Dependency summary with three keys:
            - ``"depends_on"`` (list[str]): Files this page's code imports.
            - ``"depended_by"`` (list[str]): Files that import this page's
              code.
            - ``"external_deps"`` (list[str]): Third-party packages
              referenced by this page's files.
    """
    from worker.pipeline.dependency_graph import summarize_page_deps

    return summarize_page_deps(page_spec.files or [], dep_graph)


def _prepend_architecture_diagram(content: str, diagram: str) -> str:
    """Prepend (or replace) the Architecture Mermaid block at the top of a page.

    If the page already contains an ``## Architecture`` section with a
    fenced ``mermaid`` code block, it is stripped via regex before the new
    block is prepended.  This ensures that re-running the stage does not
    accumulate duplicate diagram sections.

    Args:
        content (str): Existing Markdown content of the wiki page.
        diagram (str): Raw Mermaid diagram source (without the fence).

    Returns:
        str: Updated page content with the new Architecture block prepended.

    Example:
        >>> updated = _prepend_architecture_diagram(page_md, mermaid_src)
        >>> updated.startswith("## Architecture\\n\\n```mermaid\\n")
        True
    """
    prefix = f"## Architecture\n\n```mermaid\n{diagram}\n```\n\n"
    # Strip any existing Architecture block so we don't duplicate it
    stripped = re.sub(
        r"^## Architecture\s*\n+```mermaid\n.*?```\s*\n*",
        "",
        content,
        count=1,
        flags=re.DOTALL,
    )
    return prefix + stripped


def _make_faiss_store(repo_data_dir: Path, embedding) -> FAISSStore:
    """Instantiate a FAISSStore pointed at the repository's index files.

    Args:
        repo_data_dir (Path): Root data directory for the repository
            (e.g. ``~/.autowiki/repos/<repo_id>``).  The FAISS index and
            metadata pickle are expected (or will be written) at
            ``faiss.index`` and ``faiss.meta.pkl`` within this directory.
        embedding: Any object that exposes a ``dimension`` attribute (int)
            indicating the vector dimensionality of the embedding model.

    Returns:
        FAISSStore: Configured store instance.  The underlying index is
            not loaded from disk until the first search or explicit load
            call; it is created from scratch when ``build_rag_index`` runs.
    """
    return FAISSStore(
        dimension=embedding.dimension,
        index_path=repo_data_dir / "faiss.index",
        meta_path=repo_data_dir / "faiss.meta.pkl",
    )


# ---------------------------------------------------------------------------
# Job entry points
# ---------------------------------------------------------------------------


async def run_full_index(
    ctx: dict,
    repo_id: str,
    job_id: str,
    owner: str,
    name: str,
    clone_root: Path | None = None,
    wiki_language: str = "en",
) -> None:
    """Run the complete 7-stage wiki generation pipeline for a repository.

    This is the primary ARQ job function.  It clears all existing artifacts
    at the start of each run to ensure a clean, reproducible output, then
    executes each pipeline stage in sequence, writing progress to the DB
    after each one.

    Artifact clearing (before Stage 1):
        Removes the FAISS index, metadata pickle, all Markdown files in
        ``wiki/``, ``wiki_plan.json``, and ``architecture.mmd``.  Wiki page
        rows in SQLite are also deleted so the DB stays in sync with the
        file system.

    Pipeline stages:
        1. **Ingestion** — Shallow-clone or fetch the repo; filter source
           files via ``.autowikiignore``; extract the README.
        2. **AST Analysis** — Single-pass Tree-Sitter parse across all
           filtered files; produces a ``FileAnalysis`` with entities per
           file.
        3. **Dependency Graph** — Build file-level import graph; cluster
           related files for context-aware page planning.
        4. **RAG Indexer** — Entity-aware chunking of source files + FAISS
           ``IndexFlatIP`` build with embedding vectors.
        5. **Wiki Planner** — LLM generates a logical page hierarchy
           (``WikiPlan``) with file-to-page assignments.
        6. **Page Generator** — For each page: RAG retrieval + LLM Markdown
           generation; results written to SQLite and ``wiki/*.md``.
        7. **Architecture Diagram** — Mermaid diagram synthesised from the
           wiki plan; prepended to the first wiki page.

    Args:
        ctx (dict): ARQ context dictionary (provided automatically by the
            ARQ worker; not used directly in this function).
        repo_id (str): UUID primary key of the repository row in SQLite.
        job_id (str): UUID primary key of the job row in SQLite; used for
            progress updates throughout the run.
        owner (str): GitHub repository owner (username or organisation).
        name (str): GitHub repository name.
        clone_root (Path | None): Override the default clone directory.
            Defaults to ``<data_dir>/repos/<repo_id>/clone``.

    Returns:
        None

    Raises:
        Exception: Any unhandled exception from a pipeline stage sets the
            job status to ``"failed"`` and the repository status to
            ``"error"`` in SQLite before re-raising.
    """
    cfg = get_config()
    db_path = str(cfg.database_path)
    data_dir = cfg.data_dir
    await init_db(db_path)
    _on_retry = _make_on_retry(db_path, job_id)

    try:
        logger.info("Job starting for %s/%s", owner, name)
        await _update_job(
            db_path,
            job_id,
            status="running",
            progress=5,
            status_description="Cloning repository and fetching files...",
        )
        await _update_repo(db_path, repo_id, status="indexing")

        # Clear all artifacts from any previous run before starting fresh.
        repo_data_dir = data_dir / "repos" / repo_id
        repo_data_dir.mkdir(parents=True, exist_ok=True)

        def _clear_repo_artifacts() -> None:
            index_path = repo_data_dir / "faiss.index"
            meta_path = repo_data_dir / "faiss.meta.pkl"
            wiki_dir = repo_data_dir / "wiki"
            ast_dir = repo_data_dir / "ast"
            for p in (index_path, meta_path):
                if p.exists():
                    p.unlink()
            if wiki_dir.exists():
                for f in wiki_dir.iterdir():
                    if f.is_file():
                        f.unlink()
            for name_ in ("wiki_plan.json", "architecture.mmd"):
                p = ast_dir / name_
                if p.exists():
                    p.unlink()

        await asyncio.get_running_loop().run_in_executor(None, _clear_repo_artifacts)
        # Delete all existing wiki page rows for this repo so the DB matches disk
        async with get_session(db_path) as s:
            await s.execute(sa_delete(WikiPage).where(WikiPage.repo_id == repo_id))
            await s.commit()

        # Stage 1: Ingestion — shallow-clone or fetch the repo; filter source files
        logger.info("Stage 1: Ingestion starting for %s/%s", owner, name)
        if clone_root is None:
            clone_root = repo_data_dir / "clone"
        head_sha, active_branch = await clone_or_fetch(clone_root, owner, name)
        logger.info("Clone complete. HEAD SHA: %s, Branch: %s", head_sha, active_branch)

        # Fetch GitHub metadata (description, stars, language, default_branch)
        meta = await fetch_github_metadata(owner, name)
        # Use active branch from clone as fallback if metadata fetch fails
        if not meta.get("default_branch"):
            meta["default_branch"] = active_branch

        if any(meta.values()):
            await _update_repo(db_path, repo_id, **meta)
            logger.info("GitHub metadata fetched: %s", meta)

        loop = asyncio.get_running_loop()
        ignore_file = clone_root / ".autowikiignore"
        files = await loop.run_in_executor(
            None, lambda: filter_files(clone_root, ignore_file=ignore_file)
        )
        logger.info("Filtered files: found %d candidate files", len(files))
        readme = await loop.run_in_executor(None, extract_readme, clone_root)
        logger.info(
            "README extracted: %d chars", len(readme)
        ) if readme else logger.info("No README found")
        await _update_job(
            db_path,
            job_id,
            progress=20,
            status_description="Analyzing source code structure (AST)...",
        )

        # Stage 2: AST Analysis — single-pass Tree-Sitter parse; produces FileAnalysis
        logger.info("Stage 2: AST Analysis starting")
        ast_dir = repo_data_dir / "ast"
        ast_dir.mkdir(parents=True, exist_ok=True)
        file_analysis = await loop.run_in_executor(
            None, analyze_all_files, clone_root, files
        )
        logger.info(
            "AST analysis complete: %d files analyzed", len(file_analysis.files)
        )
        await _write_text_async(
            ast_dir / "file_analysis_summary.txt", file_analysis.to_llm_summary()
        )
        await _update_job(
            db_path,
            job_id,
            progress=35,
            status_description="Building dependency graph...",
        )

        # Stage 3: Dependency Graph — file-level import graph; used for clustering
        logger.info("Stage 3: Dependency Graph starting")
        dep_graph = build_dependency_graph(files, clone_root)
        logger.info(
            "Dependency graph built: %d nodes, %d edges",
            sum(len(c) for c in dep_graph.clusters),
            sum(len(e) for e in dep_graph.edges.values()),
        )
        await _update_job(
            db_path,
            job_id,
            progress=45,
            status_description="Indexing code for RAG search (embedding)...",
        )

        # Stage 4: RAG Indexer — entity-aware chunking + FAISS vector index
        logger.info("Stage 4: RAG Indexer starting")
        llm = make_llm_provider(cfg)
        embedding = make_embedding_provider(cfg)
        logger.info(
            "Using embedding provider: %s, model: %s (dim=%d)",
            cfg.embedding.provider,
            cfg.embedding.model,
            embedding.dimension,
        )
        index_path = repo_data_dir / "faiss.index"
        wiki_dir = repo_data_dir / "wiki"
        store = _make_faiss_store(repo_data_dir, embedding)
        file_entities = {
            rel: [e for e in info.entities] for rel, info in file_analysis.files.items()
        }
        logger.info("Building new RAG index at %s", index_path)
        await build_rag_index(
            files,
            clone_root,
            store,
            embedding,
            file_entities=file_entities,
            on_retry=_on_retry,
        )
        logger.info("RAG index build complete")
        await _update_job(
            db_path,
            job_id,
            progress=55,
            status_description="Planning wiki structure...",
        )

        # Stage 5: Wiki Planner — LLM generates logical page tree (WikiPlan)
        logger.info("Stage 5: Wiki Planner starting")
        plan = await generate_wiki_plan(
            file_analysis,
            repo_name=name,
            llm=llm,
            dep_graph=dep_graph,
            readme=readme,
            on_retry=_on_retry,
            wiki_language=wiki_language,
        )
        logger.info(
            "Wiki plan generated: %d pages planned for %s", len(plan.pages), name
        )
        wiki_dir.mkdir(exist_ok=True)
        # Write both the internal plan (with file assignments) and the user-facing JSON
        await _write_text_async(
            ast_dir / "wiki_plan.json",
            json.dumps(plan.to_internal_json(), indent=2),
        )
        await _write_text_async(
            wiki_dir / "wiki.json",
            json.dumps(plan.to_wiki_json(), indent=2),
        )
        await _update_job(db_path, job_id, progress=70)

        # Stage 6: Page Generator — RAG retrieval + LLM Markdown per page
        logger.info("Stage 6: Page Generator starting")
        total = len(plan.pages)

        for i, page_spec in enumerate(plan.pages):
            # Scale progress from 70 → 97 as pages complete
            progress = 70 + int(27 * (i + 1) / total) if total > 0 else 97
            page_entities = _collect_page_entities(page_spec, file_analysis)
            page_dep_info = _collect_page_deps(page_spec, dep_graph)
            result = await generate_page(
                page_spec,
                store,
                llm,
                embedding,
                repo_name=name,
                # Only pass dep_info / entity_details when they contain data
                dep_info=page_dep_info if any(page_dep_info.values()) else None,
                entity_details=page_entities if page_entities else None,
                on_retry=_on_retry,
                wiki_language=wiki_language,
            )
            logger.info(
                "Page generated: %s (%s), %d chars",
                result.title,
                result.slug,
                len(result.content),
            )
            async with get_session(db_path) as s:
                s.add(
                    WikiPage(
                        id=str(uuid.uuid4()),
                        repo_id=repo_id,
                        slug=result.slug,
                        title=result.title,
                        content=result.content,
                        page_order=i,
                        parent_slug=page_spec.parent_slug,
                        description=page_spec.purpose,
                    )
                )
                await s.commit()
            await _write_text_async(wiki_dir / f"{result.slug}.md", result.content)
            await _update_job(
                db_path,
                job_id,
                progress=progress,
                status_description=f"Generating page: {result.title}...",
            )

        # Stage 7: Architecture Diagram — Mermaid diagram prepended to first page
        logger.info("Stage 7: Architecture Diagram Synthesis starting")
        diagram = await synthesize_diagrams(
            plan, repo_name=name, llm=llm, wiki_language=wiki_language
        )
        if diagram is not None:
            logger.info("Architecture diagram synthesized: %d chars", len(diagram))
            if plan.pages:
                async with get_session(db_path) as s:
                    result_row = await s.execute(
                        sa_select(WikiPage).where(
                            WikiPage.repo_id == repo_id,
                            WikiPage.slug == plan.pages[0].slug,
                        )
                    )
                    first_page = result_row.scalar_one_or_none()
                    if first_page is not None:
                        first_page.content = _prepend_architecture_diagram(
                            first_page.content, diagram
                        )
                        await s.commit()
                        await _write_text_async(
                            wiki_dir / f"{first_page.slug}.md", first_page.content
                        )
            await _write_text_async(ast_dir / "architecture.mmd", diagram)
        else:
            logger.info("No architecture diagram synthesized")

        structure_data = plan.to_api_structure()
        now = datetime.now(UTC)
        logger.info("Full index job complete for %s/%s", owner, name)
        await _update_job(
            db_path,
            job_id,
            status="done",
            progress=100,
            finished_at=now,
            status_description="Wiki generation complete!",
        )
        await _update_repo(
            db_path,
            repo_id,
            status="ready",
            last_commit=head_sha,
            indexed_at=now,
            wiki_path=str(wiki_dir),
            wiki_structure=json.dumps(structure_data),
        )

    except Exception as e:
        now = datetime.now(UTC)
        logger.exception("Job failed for %s/%s: %s", owner, name, str(e))
        await _update_job(
            db_path,
            job_id,
            status="failed",
            error=str(e),
            finished_at=now,
            status_description=f"Error: {str(e)}",
        )
        await _update_repo(db_path, repo_id, status="error")
        raise


async def run_refresh_index(
    ctx: dict,
    repo_id: str,
    job_id: str,
    owner: str,
    name: str,
    clone_root: Path | None = None,
    wiki_language: str = "en",
) -> None:
    """Incremental refresh: re-run the pipeline only for pages with changed files.

    Implements an incremental update strategy that avoids regenerating the
    entire wiki when only a subset of source files have changed.  The
    function has several decision points that can escalate to a full reindex:

    Early-exit conditions (no work needed):
        - The repository HEAD SHA matches the stored ``last_commit`` — the
          repo is already up to date; the job completes immediately.

    Full-reindex fallbacks:
        - ``get_changed_files`` raises (e.g. the old SHA is no longer in the
          shallow clone's history) — diff is unavailable.
        - No ``ast/wiki_plan.json`` exists — cannot determine which pages
          are affected without the previous plan.
        - Source files were *removed* since the last index — the page-to-file
          mapping must be rebuilt from scratch.

    Incremental path (no fallback triggered):
        1. **Ingestion** — ``git fetch`` to get the new HEAD SHA.
        2. **AST Analysis** — Re-parse all current files.
        3. **Dependency Graph** — Rebuild import graph over current files.
        4. **RAG Indexer** — Rebuild the FAISS index for all current files.
        5. **Wiki Planner** — Run planning only over the files from affected
           pages (plus any newly added files), while passing the titles of
           unaffected pages as ``existing_titles`` so the LLM does not
           duplicate them.
        6. **Page Generator** — Regenerate only the newly planned pages;
           preserve the ``page_order`` of replaced pages so the navigation
           ordering stays stable.  Truly new pages are appended.
        7. **Architecture Diagram** — Rebuild diagram from the merged plan
           (new + preserved pages) and update the first wiki page.

    Page-notes merge:
        Before planning, the user-facing ``wiki.json`` is loaded and
        ``page_notes`` keyed by title are extracted.  These are merged back
        into the ``WikiPageSpec`` objects so any user-authored annotations
        survive the refresh.

    Preserved pages identification:
        Unchanged pages are identified by *title*, not slug.  If the LLM
        re-titles a page during replanning, the old slug is not accidentally
        kept; the new plan's version takes precedence.

    Args:
        ctx (dict): ARQ context dictionary (provided automatically by the
            ARQ worker; not used directly in this function).
        repo_id (str): UUID primary key of the repository row in SQLite.
        job_id (str): UUID primary key of the job row in SQLite.
        owner (str): GitHub repository owner (username or organisation).
        name (str): GitHub repository name.
        clone_root (Path | None): Override the default clone directory.
            Defaults to ``<data_dir>/repos/<repo_id>/clone``.

    Returns:
        None

    Raises:
        Exception: Any unhandled exception sets the job status to
            ``"failed"`` and the repository status to ``"error"`` before
            re-raising.
    """
    cfg = get_config()
    db_path = str(cfg.database_path)
    data_dir = cfg.data_dir
    await init_db(db_path)
    _on_retry = _make_on_retry(db_path, job_id)

    try:
        logger.info("Job starting for %s/%s", owner, name)
        await _update_job(
            db_path,
            job_id,
            status="running",
            progress=5,
            status_description="Fetching latest commits...",
        )

        # Stage 1: Clone/fetch to get new HEAD
        logger.info("Stage 1: Ingestion starting for %s/%s", owner, name)
        repo_data_dir = data_dir / "repos" / repo_id
        if clone_root is None:
            clone_root = repo_data_dir / "clone"
        new_sha, _ = await clone_or_fetch(clone_root, owner, name)
        logger.info("Fetch complete. New HEAD SHA: %s", new_sha)

        async with get_session(db_path) as s:
            repo = await s.get(Repository, repo_id)
            old_sha = repo.last_commit or ""

        # Early-exit: SHA unchanged means no commits since last index
        if old_sha == new_sha:
            logger.info(
                "Repository %s/%s is already up to date at %s", owner, name, new_sha
            )
            now = datetime.now(UTC)
            await _update_repo(db_path, repo_id, status="ready")
            await _update_job(
                db_path,
                job_id,
                status="done",
                progress=100,
                finished_at=now,
                status_description="Already up to date.",
            )
            return

        # Find changed files and affected pages.
        # Falls back to a forced full reindex if the stored SHA is unreachable
        # (e.g. shallow clone that no longer contains the base commit).
        try:
            changed_files = (
                await get_changed_files(clone_root, old_sha, new_sha) if old_sha else []
            )
            logger.info("Changed files detected: %d files", len(changed_files))
        except Exception:
            # Diff unavailable (shallow history truncated) — cannot do incremental
            logger.warning(
                "Could not calculate diff from %s to %s. Falling back to full reindex.",
                old_sha,
                new_sha,
            )
            await run_full_index(
                ctx,
                repo_id=repo_id,
                job_id=job_id,
                owner=owner,
                name=name,
                clone_root=clone_root,
                wiki_language=wiki_language,
            )
            return

        ast_dir = repo_data_dir / "ast"
        wiki_plan_path = ast_dir / "wiki_plan.json"
        # Without a stored wiki plan we cannot compute which pages are affected
        if not wiki_plan_path.exists():
            logger.info("No existing wiki plan found. Falling back to full reindex.")
            await run_full_index(
                ctx,
                repo_id=repo_id,
                job_id=job_id,
                owner=owner,
                name=name,
                clone_root=clone_root,
                wiki_language=wiki_language,
            )
            return

        content = await asyncio.get_running_loop().run_in_executor(
            None, wiki_plan_path.read_text
        )
        plan_data = json.loads(content)

        # Load user-facing wiki.json to preserve any user-edited page_notes
        wiki_json_path = repo_data_dir / "wiki" / "wiki.json"
        saved_page_notes: dict[str, list[dict]] = {}
        saved_repo_notes: list[dict] = []
        if wiki_json_path.exists():
            try:
                wiki_json_data = json.loads(
                    await asyncio.get_running_loop().run_in_executor(
                        None, wiki_json_path.read_text
                    )
                )
                saved_repo_notes = wiki_json_data.get("repo_notes", [])
                # Index page_notes by title so they can be re-injected after replanning
                for wp in wiki_json_data.get("pages", []):
                    if "title" in wp and "page_notes" in wp:
                        saved_page_notes[wp["title"]] = wp["page_notes"]
            except Exception:
                pass  # Corrupt or missing wiki.json — proceed without notes

        old_plan = WikiPlan(
            repo_notes=(
                saved_repo_notes or plan_data.get("repo_notes", [{"content": ""}])
            ),
            pages=[
                WikiPageSpec(
                    title=p["title"],
                    purpose=p.get("purpose", ""),
                    parent=p.get("parent"),
                    files=p.get("files", []),
                    # Merge saved page_notes back into the spec; default to empty note
                    page_notes=saved_page_notes.get(p["title"], [{"content": ""}]),
                )
                for p in plan_data.get("pages", [])
            ],
        )

        affected_page_titles = get_affected_pages(changed_files, old_plan)
        if not affected_page_titles:
            logger.info("No affected pages found for changed files.")
            now = datetime.now(UTC)
            await _update_repo(db_path, repo_id, last_commit=new_sha, status="ready")
            await _update_job(
                db_path,
                job_id,
                status="done",
                progress=100,
                finished_at=now,
                status_description="No affected pages found.",
            )
            return

        logger.info("Affected pages: %s", ", ".join(affected_page_titles))
        await _update_job(
            db_path,
            job_id,
            progress=20,
            status_description="Analyzing updated source code...",
        )

        # Stage 2: Re-analyze AST
        logger.info("Stage 2: AST Analysis starting")
        loop = asyncio.get_running_loop()
        ignore_file = clone_root / ".autowikiignore"
        files = await loop.run_in_executor(
            None, lambda: filter_files(clone_root, ignore_file=ignore_file)
        )
        logger.info("Filtered files: found %d candidate files", len(files))
        readme = await loop.run_in_executor(None, extract_readme, clone_root)
        if readme:
            logger.info("README extracted: %d chars", len(readme))
        file_analysis = await loop.run_in_executor(
            None, analyze_all_files, clone_root, files
        )
        logger.info(
            "AST analysis complete: %d files analyzed", len(file_analysis.files)
        )
        ast_dir.mkdir(parents=True, exist_ok=True)
        await _write_text_async(
            ast_dir / "file_analysis_summary.txt", file_analysis.to_llm_summary()
        )

        # Detect structural changes: added or removed files relative to the old plan
        old_all_files = {f for p in old_plan.pages for f in (p.files or [])}
        new_all_files = set(file_analysis.files.keys())
        added_files = new_all_files - old_all_files
        removed_files = old_all_files - new_all_files

        # Removed files break the page→file mapping; a full reindex is safer
        if removed_files:
            logger.info(
                "Removed files detected (%s). Falling back to full reindex.",
                ", ".join(sorted(removed_files)),
            )
            await run_full_index(
                ctx,
                repo_id=repo_id,
                job_id=job_id,
                owner=owner,
                name=name,
                clone_root=clone_root,
                wiki_language=wiki_language,
            )
            return

        if added_files:
            logger.info("Added files detected: %s", ", ".join(sorted(added_files)))
            # Include affected pages' titles and add new files to the Overview page
            # (or the first page if no Overview exists)
            overview_page = next(
                (p for p in old_plan.pages if "overview" in p.title.lower()),
                old_plan.pages[0] if old_plan.pages else None,
            )
            if overview_page is not None:
                # Mark the overview page as affected so new files are surfaced there
                affected_page_titles = affected_page_titles | {overview_page.title}

        await _update_job(
            db_path,
            job_id,
            progress=30,
            status_description="Rebuilding dependency graph...",
        )

        # Stage 3: Dependency Graph
        logger.info("Stage 3: Dependency Graph starting")
        dep_graph = build_dependency_graph(files, clone_root)
        logger.info(
            "Dependency graph built: %d nodes, %d edges",
            sum(len(c) for c in dep_graph.clusters),
            sum(len(e) for e in dep_graph.edges.values()),
        )
        await _update_job(
            db_path, job_id, progress=40, status_description="Rebuilding RAG index..."
        )

        # Stage 4: Rebuild FAISS index
        logger.info("Stage 4: RAG Indexer starting")
        llm = make_llm_provider(cfg)
        embedding = make_embedding_provider(cfg)
        logger.info(
            "Using embedding provider: %s, model: %s (dim=%d)",
            cfg.embedding.provider,
            cfg.embedding.model,
            embedding.dimension,
        )
        repo_data_dir.mkdir(parents=True, exist_ok=True)
        store = _make_faiss_store(repo_data_dir, embedding)
        file_entities = {
            rel: [e for e in info.entities] for rel, info in file_analysis.files.items()
        }
        logger.info("Rebuilding RAG index...")
        await build_rag_index(
            files,
            clone_root,
            store,
            embedding,
            file_entities=file_entities,
            on_retry=_on_retry,
        )
        logger.info("RAG index build complete")
        await _update_job(
            db_path,
            job_id,
            progress=55,
            status_description="Re-planning updated wiki pages...",
        )

        # Stage 5: Re-plan for affected pages
        logger.info(
            "Stage 5: Wiki Planner starting for %d affected pages",
            len(affected_page_titles),
        )
        # Build a FileAnalysis containing only files from the affected pages so the
        # planner focuses its token budget on the changed code surface area.
        affected_files_set = {
            f
            for p in old_plan.pages
            if p.title in affected_page_titles
            for f in (p.files or [])
        }
        affected_files_set |= added_files
        affected_file_analysis = FileAnalysis(
            files={
                rel: info
                for rel, info in file_analysis.files.items()
                if rel in affected_files_set
            }
        )
        # Pass unaffected titles so the planner doesn't generate duplicate pages
        unaffected_titles = {
            p.title for p in old_plan.pages if p.title not in affected_page_titles
        }
        plan = await generate_wiki_plan(
            affected_file_analysis,
            repo_name=name,
            llm=llm,
            dep_graph=dep_graph,
            readme=readme,
            on_retry=_on_retry,
            existing_titles=unaffected_titles,
            wiki_language=wiki_language,
        )
        logger.info(
            "Wiki plan generated: %d pages updated for %s", len(plan.pages), name
        )
        await _update_job(db_path, job_id, progress=65)

        # Collect slugs of the affected OLD pages — these are what we delete.
        # Using old slugs (not new) handles the case where the LLM retitles a page.
        affected_old_slugs = {
            p.slug for p in old_plan.pages if p.title in affected_page_titles
        }

        # Capture existing page_orders before deletion to preserve stable ordering
        old_page_orders: dict[str, int] = {}
        max_existing_order = 0
        async with get_session(db_path) as s:
            result = await s.execute(
                sa_select(WikiPage).where(WikiPage.repo_id == repo_id)
            )
            for p in result.scalars().all():
                if p.slug in affected_old_slugs:
                    old_page_orders[p.slug] = p.page_order
                max_existing_order = max(max_existing_order, p.page_order)

        async with get_session(db_path) as s:
            await s.execute(
                sa_delete(WikiPage).where(
                    WikiPage.repo_id == repo_id, WikiPage.slug.in_(affected_old_slugs)
                )
            )
            await s.commit()

        # Stage 6: Regenerate pages
        logger.info("Stage 6: Page Generator starting")
        wiki_dir = repo_data_dir / "wiki"
        wiki_dir.mkdir(exist_ok=True)
        total = len(plan.pages)

        for i, page_spec in enumerate(plan.pages):
            page_entities = _collect_page_entities(page_spec, file_analysis)
            page_dep_info = _collect_page_deps(page_spec, dep_graph)
            result = await generate_page(
                page_spec,
                store,
                llm,
                embedding,
                repo_name=name,
                dep_info=page_dep_info if any(page_dep_info.values()) else None,
                entity_details=page_entities if page_entities else None,
                on_retry=_on_retry,
                wiki_language=wiki_language,
            )
            logger.info(
                "Page updated: %s (%s), %d chars",
                result.title,
                result.slug,
                len(result.content),
            )
            # Preserve original page_order for replaced pages; append truly new ones
            page_order = old_page_orders.get(result.slug, max_existing_order + 1 + i)
            async with get_session(db_path) as s:
                s.add(
                    WikiPage(
                        id=str(uuid.uuid4()),
                        repo_id=repo_id,
                        slug=result.slug,
                        title=result.title,
                        content=result.content,
                        page_order=page_order,
                        parent_slug=page_spec.parent_slug,
                        description=page_spec.purpose,
                    )
                )
                await s.commit()
            await _write_text_async(wiki_dir / f"{result.slug}.md", result.content)
            progress = 65 + int(30 * (i + 1) / total) if total > 0 else 95
            await _update_job(
                db_path,
                job_id,
                progress=progress,
                status_description=f"Regenerating page: {result.title}...",
            )

        # Build a merged plan reflecting the full updated wiki structure.
        # Unchanged pages are identified by title, not slug, so a retitled page
        # in the new plan doesn't accidentally preserve the stale old entry.
        preserved_pages = [
            p for p in old_plan.pages if p.title not in affected_page_titles
        ]
        merged_pages = list(plan.pages) + preserved_pages
        merged_plan = WikiPlan(repo_notes=old_plan.repo_notes, pages=merged_pages)

        # Stage 7: Rebuild architecture diagram and update first wiki page
        logger.info("Stage 7: Architecture Diagram Synthesis starting")
        diagram = await synthesize_diagrams(
            merged_plan, repo_name=name, llm=llm, wiki_language=wiki_language
        )
        if diagram:
            logger.info("Architecture diagram synthesized: %d chars", len(diagram))
            await _write_text_async(ast_dir / "architecture.mmd", diagram)
            async with get_session(db_path) as s:
                result_row = await s.execute(
                    sa_select(WikiPage)
                    .where(WikiPage.repo_id == repo_id)
                    .order_by(WikiPage.page_order)
                    .limit(1)
                )
                first_page = result_row.scalar_one_or_none()
                if first_page is not None:
                    first_page.content = _prepend_architecture_diagram(
                        first_page.content, diagram
                    )
                    await s.commit()
                    wiki_dir.mkdir(parents=True, exist_ok=True)
                    await _write_text_async(
                        wiki_dir / f"{first_page.slug}.md", first_page.content
                    )
        else:
            logger.info("No architecture diagram synthesized")

        # Persist the merged plan so future refreshes have an accurate baseline
        wiki_dir.mkdir(parents=True, exist_ok=True)
        await _write_text_async(
            ast_dir / "wiki_plan.json",
            json.dumps(merged_plan.to_internal_json(), indent=2),
        )
        await _write_text_async(
            wiki_dir / "wiki.json",
            json.dumps(merged_plan.to_wiki_json(), indent=2),
        )
        structure_data = merged_plan.to_api_structure()

        now = datetime.now(UTC)
        logger.info("Incremental refresh job complete for %s/%s", owner, name)
        await _update_job(
            db_path,
            job_id,
            status="done",
            progress=100,
            finished_at=now,
            status_description="Refresh complete!",
        )
        await _update_repo(
            db_path,
            repo_id,
            status="ready",
            last_commit=new_sha,
            indexed_at=now,
            wiki_path=str(wiki_dir),
            wiki_structure=json.dumps(structure_data),
        )

    except Exception as e:
        now = datetime.now(UTC)
        logger.exception("Job failed for %s/%s: %s", owner, name, str(e))
        await _update_job(
            db_path,
            job_id,
            status="failed",
            error=str(e),
            finished_at=now,
            status_description=f"Error: {str(e)}",
        )
        await _update_repo(db_path, repo_id, status="error")
        raise
