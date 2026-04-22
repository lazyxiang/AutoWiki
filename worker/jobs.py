"""ARQ job functions that orchestrate the 6-stage wiki generation pipeline.

Registered in ``worker/main.py`` as background tasks executed by the ARQ
worker.  Two entry points are exposed:

- ``run_full_index``: Complete pipeline from scratch — clears all previous
  artifacts and runs all 6 stages for a repository.  Pass
  ``reuse_index=True`` to skip Stage 4 (RAG embedding) when a FAISS index
  already exists for this repository.
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
from worker.llm import make_fast_llm_provider, make_llm_provider
from worker.pipeline.ast_analysis import FileAnalysis, analyze_all_files
from worker.pipeline.dependency_graph import build_dependency_graph
from worker.pipeline.ingestion import (
    clone_or_fetch,
    extract_readme,
    filter_files,
    get_affected_pages,
    get_changed_files,
)
from worker.pipeline.page_generator import (
    PageResult,
    compute_generation_order,
    generate_page_batch,
)
from worker.pipeline.rag_indexer import FAISSStore, build_rag_index
from worker.pipeline.user_steering import load_user_steering
from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan, generate_wiki_plan
from worker.platform.registry import get_platform_by_name
from worker.platform.token_store import get_platform_token

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


def asdict_s(obj) -> dict:
    """dataclasses.asdict proxy for the Deep Research ARQ job."""
    from dataclasses import asdict as _asdict

    return _asdict(obj)


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


async def _load_faiss_for_research(repo_data_dir: Path, embedding) -> FAISSStore:
    """Load the FAISS store for a repo, running the blocking IO in an executor."""
    store = _make_faiss_store(repo_data_dir, embedding)
    await asyncio.get_running_loop().run_in_executor(None, store.load)
    return store


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
    reuse_index: bool = False,
    reuse_plan: bool = False,
) -> None:
    """Run the complete 6-stage wiki generation pipeline for a repository.

    This is the primary ARQ job function.  It clears all existing artifacts
    at the start of each run to ensure a clean, reproducible output, then
    executes each pipeline stage in sequence, writing progress to the DB
    after each one.

    Artifact clearing (before Stage 1):
        Removes all Markdown files in ``wiki/`` and ``wiki_plan.json``.
        Wiki page rows in SQLite are also deleted so the DB stays in sync
        with the file system.  When *reuse_index* is ``True`` the FAISS
        index and metadata files are preserved so Stage 4 can be skipped.

    Pipeline stages:
        1. **Ingestion** — Shallow-clone or fetch the repo; filter source
           files via ``.autowikiignore``; extract the README.
        2. **AST Analysis** — Single-pass Tree-Sitter parse across all
           filtered files; produces a ``FileAnalysis`` with entities per
           file.
        3. **Dependency Graph** — Build file-level import graph; cluster
           related files for context-aware page planning.
        4. **RAG Indexer** — Entity-aware chunking of source files + FAISS
           ``IndexFlatIP`` build with embedding vectors.  Skipped when
           *reuse_index* is ``True`` and a FAISS index already exists.
        5. **Wiki Planner** — LLM generates a logical page hierarchy
           (``WikiPlan``) with file-to-page assignments.
        6. **Page Generator** — For each page: RAG retrieval + LLM Markdown
           generation; results written to SQLite and ``wiki/*.md``.

    Args:
        ctx (dict): ARQ context dictionary (provided automatically by the
            ARQ worker; not used directly in this function).
        repo_id (str): UUID primary key of the repository row in SQLite.
        job_id (str): UUID primary key of the job row in SQLite; used for
            progress updates throughout the run.
        owner (str): Repository owner (username or organisation).
        name (str): Repository name.
        clone_root (Path | None): Override the default clone directory.
            Defaults to ``<data_dir>/repos/<repo_id>/clone``.
        reuse_index (bool): When ``True``, preserve any existing FAISS index
            and skip Stage 4 (RAG Indexer) if the index file is present.
            Useful for iterating on wiki structure without re-embedding.
            Defaults to ``False``.
        reuse_plan (bool): When ``True``, skip Stage 5 (Wiki Planner) and
            load ``ast/wiki_plan.json`` directly if it exists.  User-edited
            ``page_notes`` from ``wiki/wiki.json`` are preserved.
            Defaults to ``False``.

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
            """Remove generated wiki files and optionally the search index.

            Always removes all Markdown pages in ``wiki/`` and the internal
            wiki plan.  When *reuse_index* is ``False`` the FAISS index and
            metadata pickle are also deleted.  The git clone is preserved.
            """
            index_path = repo_data_dir / "faiss.index"
            meta_path = repo_data_dir / "faiss.meta.pkl"
            wiki_dir = repo_data_dir / "wiki"
            ast_dir = repo_data_dir / "ast"
            if not reuse_index:
                for p in (index_path, meta_path):
                    if p.exists():
                        p.unlink()
            if wiki_dir.exists():
                for f in wiki_dir.iterdir():
                    if f.is_file():
                        f.unlink()
            if not reuse_plan:
                wiki_plan = ast_dir / "wiki_plan.json"
                if wiki_plan.exists():
                    wiki_plan.unlink()

        await asyncio.get_running_loop().run_in_executor(None, _clear_repo_artifacts)
        # Delete all existing wiki page rows for this repo so the DB matches disk
        async with get_session(db_path) as s:
            await s.execute(sa_delete(WikiPage).where(WikiPage.repo_id == repo_id))
            await s.commit()

        # Stage 1: Ingestion — detect platform, fetch token, clone, filter files
        logger.info("Stage 1: Ingestion starting for %s/%s", owner, name)
        if clone_root is None:
            clone_root = repo_data_dir / "clone"

        async with get_session(db_path) as s:
            repo_row = await s.get(Repository, repo_id)
            platform_name = (repo_row.platform if repo_row else None) or "github"
            token = await get_platform_token(platform_name, s)

        platform = get_platform_by_name(platform_name)
        meta = await platform.fetch_metadata(owner, name, token)
        clone_url = platform.authenticated_clone_url(owner, name, token)
        head_sha, active_branch = await clone_or_fetch(clone_root, clone_url)
        logger.info("Clone complete. HEAD SHA: %s, Branch: %s", head_sha, active_branch)

        default_branch = meta.default_branch or active_branch
        await _update_repo(
            db_path,
            repo_id,
            description=meta.description,
            stars=meta.stars,
            language=meta.language,
            default_branch=default_branch,
            is_private=meta.is_private,
        )
        logger.info("Platform metadata fetched: %s", meta)

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
        user_steering = await loop.run_in_executor(None, load_user_steering, clone_root)
        if user_steering is not None:
            logger.info(
                "Loaded .autowiki/wiki.json: %d repo_notes, %d user pages",
                len(user_steering.repo_notes),
                len(user_steering.pages),
            )
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
        fast_llm = make_fast_llm_provider(cfg, llm)
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
        if reuse_index and index_path.exists():
            logger.info(
                "Reusing existing FAISS index at %s (skipping embedding)", index_path
            )
            await loop.run_in_executor(None, store.load)
        else:
            file_entities = {
                rel: [e for e in info.entities]
                for rel, info in file_analysis.files.items()
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
        wiki_plan_path = ast_dir / "wiki_plan.json"
        if reuse_plan and wiki_plan_path.exists():
            logger.info(
                "Reusing existing wiki plan at %s (skipping LLM planning)",
                wiki_plan_path,
            )
            plan_raw = await asyncio.get_running_loop().run_in_executor(
                None, wiki_plan_path.read_text
            )
            plan_data = json.loads(plan_raw)
            # Preserve user-edited page_notes from wiki.json
            # (not stored in wiki_plan.json)
            saved_page_notes: dict[str, list[dict]] = {}
            wiki_json_path = wiki_dir / "wiki.json"
            if wiki_json_path.exists():
                try:
                    wj_raw = await asyncio.get_running_loop().run_in_executor(
                        None, wiki_json_path.read_text
                    )
                    for wp in json.loads(wj_raw).get("pages", []):
                        if "title" in wp and "page_notes" in wp:
                            saved_page_notes[wp["title"]] = wp["page_notes"]
                except Exception:
                    pass
            plan = WikiPlan(
                repo_notes=plan_data.get("repo_notes", [{"content": ""}]),
                all_repo_files=sorted(file_analysis.files.keys()),
                pages=[
                    WikiPageSpec(
                        title=p["title"],
                        purpose=p.get("purpose", ""),
                        parent=p.get("parent"),
                        files=p.get("files", []),
                        page_notes=saved_page_notes.get(p["title"], [{"content": ""}]),
                    )
                    for p in plan_data.get("pages", [])
                ],
            )
        else:
            plan = await generate_wiki_plan(
                file_analysis,
                repo_name=name,
                llm=llm,
                dep_graph=dep_graph,
                readme=readme,
                on_retry=_on_retry,
                wiki_language=wiki_language,
                fast_llm=fast_llm,
                user_steering=user_steering,
                clone_root=clone_root,
            )
        logger.info(
            "Wiki plan generated: %d pages planned for %s", len(plan.pages), name
        )
        wiki_dir.mkdir(exist_ok=True)
        # Write both the internal plan (with file assignments) and the user-facing JSON
        await _write_text_async(
            ast_dir / "wiki_plan.json",
            json.dumps(plan.to_internal_json(), indent=2, ensure_ascii=False),
        )
        await _write_text_async(
            wiki_dir / "wiki.json",
            json.dumps(plan.to_wiki_json(), indent=2, ensure_ascii=False),
        )
        await _update_job(
            db_path,
            job_id,
            progress=70,
            status_description="Generating wiki pages...",
        )

        # Stage 6: Bottom-up page generation
        logger.info("Stage 6: Page Generator starting (bottom-up)")
        total = len(plan.pages)

        levels = compute_generation_order(plan)
        generated: dict[str, PageResult] = {}
        page_order_counter = 0

        for depth_idx, level in enumerate(levels):
            specs_with_children: list[tuple[WikiPageSpec, list[PageResult] | None]] = []
            for page_spec in level:
                children = [
                    generated[p.slug]
                    for p in plan.pages
                    if p.parent == page_spec.title and p.slug in generated
                ]
                specs_with_children.append((page_spec, children or None))

            results = await generate_page_batch(
                specs_with_children,
                store,
                llm,
                fast_llm,
                embedding,
                repo_name=name,
                file_analysis=file_analysis,
                dep_graph=dep_graph,
                on_retry=_on_retry,
                wiki_language=wiki_language,
                repo_notes=plan.repo_notes or None,
            )

            for result, (page_spec, _) in zip(results, specs_with_children):
                generated[result.slug] = result
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
                            page_order=page_order_counter,
                            parent_slug=page_spec.parent_slug,
                            description=page_spec.purpose,
                        )
                    )
                    await s.commit()
                await _write_text_async(wiki_dir / f"{result.slug}.md", result.content)
                page_order_counter += 1

            pages_done = sum(len(lvl) for lvl in levels[: depth_idx + 1])
            progress = 70 + int(27 * pages_done / total) if total > 0 else 97
            level_info = f"{depth_idx + 1}/{len(levels)}"
            await _update_job(
                db_path,
                job_id,
                progress=progress,
                status_description=f"Generating pages (level {level_info})...",
            )

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
            wiki_structure=json.dumps(structure_data, ensure_ascii=False),
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
        owner (str): Repository owner (username or organisation).
        name (str): Repository name.
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
        async with get_session(db_path) as s:
            repo_row = await s.get(Repository, repo_id)
            platform_name = (repo_row.platform if repo_row else None) or "github"
            token = await get_platform_token(platform_name, s)
        platform = get_platform_by_name(platform_name)
        clone_url = platform.authenticated_clone_url(owner, name, token)
        new_sha, _ = await clone_or_fetch(clone_root, clone_url)
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

        loop = asyncio.get_running_loop()
        content = await loop.run_in_executor(None, wiki_plan_path.read_text)

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
            all_repo_files=plan_data.get("all_repo_files", []),
        )

        affected = get_affected_pages(changed_files, old_plan)
        logger.info(
            "Refresh affects %d pages",
            len(affected.primary),
        )
        affected_page_titles = affected.primary

        # Check whether any changed file is absent from the old plan — those are
        # newly-added files that get_affected_pages cannot surface (they have no
        # page mapping yet), so we must not skip the refresh early.
        old_all_files_early = (
            set(old_plan.all_repo_files)
            if old_plan.all_repo_files
            else {f for p in old_plan.pages for f in (p.files or [])}
        )
        potentially_added = set(changed_files) - old_all_files_early

        if not affected_page_titles and not potentially_added:
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
        user_steering = await loop.run_in_executor(None, load_user_steering, clone_root)
        if user_steering is not None:
            logger.info(
                "User steering loaded: %d repo_notes, %d page overrides",
                len(user_steering.repo_notes),
                len(user_steering.pages),
            )
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
        old_all_files = (
            set(old_plan.all_repo_files)
            if old_plan.all_repo_files
            else {f for p in old_plan.pages for f in (p.files or [])}
        )
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
        fast_llm = make_fast_llm_provider(cfg, llm)
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
            fast_llm=fast_llm,
            user_steering=user_steering,
            clone_root=clone_root,
        )
        logger.info(
            "Wiki plan generated: %d pages updated for %s", len(plan.pages), name
        )
        await _update_job(
            db_path,
            job_id,
            progress=65,
            status_description="Generating wiki pages...",
        )

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

        # Stage 6: Bottom-up regeneration
        logger.info("Stage 6: Page Generator starting (bottom-up)")

        wiki_dir = repo_data_dir / "wiki"
        wiki_dir.mkdir(exist_ok=True)

        # Load preserved pages from disk so they can serve as child content
        preserved_content: dict[str, PageResult] = {}
        for p in old_plan.pages:
            if p.title not in affected_page_titles:
                md_path = wiki_dir / f"{p.slug}.md"
                if md_path.exists():
                    content = await asyncio.get_running_loop().run_in_executor(
                        None, md_path.read_text
                    )
                    preserved_content[p.slug] = PageResult(
                        slug=p.slug, title=p.title, content=content
                    )

        levels = compute_generation_order(plan)
        generated: dict[str, PageResult] = {}
        refresh_order_counter = 0

        for depth_idx, level in enumerate(levels):
            specs_with_children: list[tuple[WikiPageSpec, list[PageResult] | None]] = []
            for page_spec in level:
                children = []
                for p in plan.pages:
                    if p.parent == page_spec.title:
                        if p.slug in generated:
                            children.append(generated[p.slug])
                        elif p.slug in preserved_content:
                            children.append(preserved_content[p.slug])
                # Also check old_plan for preserved children
                for p in old_plan.pages:
                    if p.parent == page_spec.title and p.slug in preserved_content:
                        if not any(c.slug == p.slug for c in children):
                            children.append(preserved_content[p.slug])
                specs_with_children.append((page_spec, children or None))

            results = await generate_page_batch(
                specs_with_children,
                store,
                llm,
                fast_llm,
                embedding,
                repo_name=name,
                file_analysis=file_analysis,
                dep_graph=dep_graph,
                on_retry=_on_retry,
                wiki_language=wiki_language,
                repo_notes=plan.repo_notes or None,
            )

            for result, (page_spec, _) in zip(results, specs_with_children):
                generated[result.slug] = result
                logger.info(
                    "Page updated: %s (%s), %d chars",
                    result.title,
                    result.slug,
                    len(result.content),
                )
                page_order = old_page_orders.get(
                    result.slug, max_existing_order + 1 + refresh_order_counter
                )
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
                refresh_order_counter += 1
                await _write_text_async(wiki_dir / f"{result.slug}.md", result.content)

            progress = 65 + int(30 * (depth_idx + 1) / len(levels)) if levels else 95
            level_info = f"{depth_idx + 1}/{len(levels)}"
            await _update_job(
                db_path,
                job_id,
                progress=progress,
                status_description=f"Regenerating pages (level {level_info})...",
            )

        # Build a merged plan reflecting the full updated wiki structure.
        # Unchanged pages are identified by title, not slug, so a retitled page
        # in the new plan doesn't accidentally preserve the stale old entry.
        preserved_pages = [
            p for p in old_plan.pages if p.title not in affected_page_titles
        ]
        merged_pages = list(plan.pages) + preserved_pages
        merged_plan = WikiPlan(
            repo_notes=old_plan.repo_notes,
            pages=merged_pages,
            all_repo_files=sorted(new_all_files),
        )

        # Persist the merged plan so future refreshes have an accurate baseline
        wiki_dir.mkdir(parents=True, exist_ok=True)
        await _write_text_async(
            ast_dir / "wiki_plan.json",
            json.dumps(merged_plan.to_internal_json(), indent=2, ensure_ascii=False),
        )
        await _write_text_async(
            wiki_dir / "wiki.json",
            json.dumps(merged_plan.to_wiki_json(), indent=2, ensure_ascii=False),
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
            wiki_structure=json.dumps(structure_data, ensure_ascii=False),
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


async def run_deep_research(
    ctx: dict,
    repo_id: str,
    job_id: str,
    report_id: str,
    question: str,
) -> None:
    """ARQ job: run the Deep Research flow and persist the result."""
    import json as _json

    from shared.models import ResearchReport
    from worker.deep_research import run_deep_research_flow

    cfg = get_config()
    db_path = str(cfg.database_path)
    data_dir = cfg.data_dir
    await init_db(db_path)

    async def _update_report(**kwargs):
        async with get_session(db_path) as s:
            report = await s.get(ResearchReport, report_id)
            if report is None:
                raise RuntimeError(f"ResearchReport {report_id!r} not found")
            for k, v in kwargs.items():
                setattr(report, k, v)
            await s.commit()

    try:
        await _update_job(db_path, job_id, status="running", progress=5)
        await _update_report(status="running")

        repo_data_dir = data_dir / "repos" / repo_id
        clone_root = repo_data_dir / "clone"

        loop = asyncio.get_running_loop()
        readme = await loop.run_in_executor(None, extract_readme, clone_root)

        embedding = make_embedding_provider(cfg)
        llm = make_llm_provider(cfg)
        store = await _load_faiss_for_research(repo_data_dir, embedding)

        async with get_session(db_path) as s:
            repo = await s.get(Repository, repo_id)
            repo_name = repo.name if repo is not None else repo_id

        async def _on_event(event: dict) -> None:
            if event["type"] == "plan":
                await _update_report(plan_json=_json.dumps(event["plan"]))
                await _update_job(db_path, job_id, progress=20)
            elif event["type"] == "step_start":
                await _update_job(
                    db_path,
                    job_id,
                    status_description=(
                        f"Investigating step {event['step_index'] + 1}"
                    ),
                )
            elif event["type"] == "step_finding":
                async with get_session(db_path) as s:
                    rep = await s.get(ResearchReport, report_id)
                    findings = _json.loads(rep.findings_json or "[]")
                    findings.append(
                        {
                            "step_index": event["step_index"],
                            "answer": event["answer"],
                            "sources": event["sources"],
                        }
                    )
                    rep.findings_json = _json.dumps(findings)
                    await s.commit()
            elif event["type"] == "report":
                await _update_report(report_markdown=event["content"])

        result = await run_deep_research_flow(
            question=question,
            repo_name=repo_name,
            readme=readme,
            store=store,
            llm=llm,
            embedding=embedding,
            on_event=_on_event,
        )

        now = datetime.now(UTC)
        await _update_report(
            status="done",
            finished_at=now,
            plan_json=_json.dumps([asdict_s(s) for s in result.plan]),
            findings_json=_json.dumps([asdict_s(f) for f in result.findings]),
            report_markdown=result.report,
        )
        await _update_job(
            db_path,
            job_id,
            status="done",
            progress=100,
            finished_at=now,
            status_description="Research complete",
        )
    except Exception as e:
        logger.exception("Deep research job failed: %s", e)
        now = datetime.now(UTC)
        await _update_report(status="failed", error=str(e), finished_at=now)
        await _update_job(
            db_path,
            job_id,
            status="failed",
            error=str(e),
            finished_at=now,
            status_description=f"Error: {e}",
        )
        raise
