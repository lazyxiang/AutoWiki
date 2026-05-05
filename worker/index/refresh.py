"""Incremental refresh ARQ job orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete as sa_delete
from sqlalchemy import select as sa_select

from shared.config import get_config
from shared.database import get_session, init_db
from shared.models import Repository, WikiPage
from worker.embedding import make_embedding_provider
from worker.index.artifacts import (
    _make_faiss_store,
    _write_text_async,
)
from worker.index.full import _phase1_prompt_dump_path, run_full_index
from worker.index.progress import (
    _make_on_retry,
    _make_page_progress_callback,
    _update_job,
    _update_repo,
)
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
from worker.pipeline.rag_indexer import build_rag_index
from worker.pipeline.retrieval.repo_index import build_repo_index
from worker.pipeline.user_steering import load_user_steering
from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan, generate_wiki_plan
from worker.platform.registry import get_platform_by_name
from worker.platform.token_store import get_platform_token

logger = logging.getLogger("worker.task")

_LEGACY_REPO_INDEX_NAME = "fast_report_index.json"
_LEGACY_FILE_ANALYSIS_SUMMARY_NAME = "file_analysis_summary.txt"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _repo_metadata_updates(meta, active_branch: str) -> dict:
    default_branch = meta.default_branch or active_branch
    updates = {"default_branch": default_branch}
    if meta.complete:
        updates.update(
            description=meta.description,
            stars=meta.stars,
            language=meta.language,
            is_private=meta.is_private,
        )
    return updates


def _remove_stale_ast_artifacts(ast_dir: Path) -> None:
    for name in (_LEGACY_REPO_INDEX_NAME, _LEGACY_FILE_ANALYSIS_SUMMARY_NAME):
        stale_path = ast_dir / name
        if stale_path.exists():
            stale_path.unlink()


def _is_global_planner_input(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    name = normalized.rsplit("/", 1)[-1]
    return (
        name.startswith("readme.")
        or normalized == ".autowiki/wiki.json"
        or normalized.endswith("/.autowiki/wiki.json")
    )


def _merge_refresh_plan_pages(
    old_pages: list[WikiPageSpec],
    replacement_pages: list[WikiPageSpec],
    affected_titles: set[str],
) -> list[WikiPageSpec]:
    """Replace affected pages in their old positions and append new pages."""
    replacements_by_title = {p.title: p for p in replacement_pages}
    used_replacement_ids: set[int] = set()
    merged: list[WikiPageSpec] = []

    def _next_unused_replacement() -> WikiPageSpec | None:
        for replacement in replacement_pages:
            if id(replacement) not in used_replacement_ids:
                return replacement
        return None

    for old_page in old_pages:
        if old_page.title not in affected_titles:
            merged.append(old_page)
            continue

        replacement = replacements_by_title.get(old_page.title)
        if replacement is None or id(replacement) in used_replacement_ids:
            replacement = _next_unused_replacement()
        if replacement is not None:
            merged.append(replacement)
            used_replacement_ids.add(id(replacement))

    for replacement in replacement_pages:
        if id(replacement) not in used_replacement_ids:
            merged.append(replacement)
            used_replacement_ids.add(id(replacement))

    return merged


# ---------------------------------------------------------------------------
# Job entry points
# ---------------------------------------------------------------------------


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
        global_planner_input_changed = any(
            _is_global_planner_input(path) for path in changed_files
        )

        if (
            not affected_page_titles
            and not potentially_added
            and not global_planner_input_changed
        ):
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
            progress=12,
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
        if global_planner_input_changed:
            logger.info(
                "Global planner inputs changed; replanning all wiki pages for refresh."
            )
            affected_page_titles = {p.title for p in old_plan.pages}

        await _update_job(
            db_path,
            job_id,
            progress=20,
            status_description="Rebuilding dependency graph...",
        )

        # Stage 3: Dependency Graph
        logger.info("Stage 3: Dependency Graph starting")
        dep_graph = await loop.run_in_executor(
            None, build_dependency_graph, files, clone_root
        )
        logger.info(
            "Dependency graph built: %d nodes, %d edges",
            sum(len(c) for c in dep_graph.clusters),
            sum(len(e) for e in dep_graph.edges.values()),
        )
        repo_index = await loop.run_in_executor(
            None,
            lambda: build_repo_index(
                root=clone_root,
                files=files,
                file_analysis=file_analysis,
                dep_graph=dep_graph,
                readme=readme,
            ),
        )
        await _write_text_async(
            ast_dir / "repo_index.json",
            json.dumps(repo_index, indent=2, ensure_ascii=False),
        )
        await _update_job(
            db_path, job_id, progress=30, status_description="Rebuilding RAG index..."
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
            progress=40,
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
        if global_planner_input_changed:
            affected_files_set = set(file_analysis.files)
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
            debug_prompt_dump_path=_phase1_prompt_dump_path(repo_data_dir),
        )
        logger.info(
            "Wiki plan generated: %d pages updated for %s", len(plan.pages), name
        )
        await _update_job(
            db_path,
            job_id,
            progress=50,
            status_description="Generating wiki pages...",
        )

        # Collect slugs of the affected OLD pages — these are what we delete.
        # Using old slugs (not new) handles the case where the LLM retitles a page.
        affected_old_slugs = {
            p.slug for p in old_plan.pages if p.title in affected_page_titles
        }

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
        staged_pages: dict[str, tuple[PageResult, WikiPageSpec]] = {}
        pages_done = 0
        total_pages = len(plan.pages)
        on_page_progress = _make_page_progress_callback(db_path, job_id, "Regenerating")
        save_lock = asyncio.Lock()

        for level in levels:
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

            async def _save_regenerated_page(
                result: PageResult, page_spec: WikiPageSpec
            ) -> None:
                nonlocal pages_done
                async with save_lock:
                    generated[result.slug] = result
                    staged_pages[result.slug] = (result, page_spec)
                    logger.info(
                        "Page regenerated and staged: %s (%s), %d chars",
                        result.title,
                        result.slug,
                        len(result.content),
                    )
                    pages_done += 1
                    progress = (
                        50 + int(48 * pages_done / total_pages)
                        if total_pages > 0
                        else 98
                    )
                    await _update_job(
                        db_path,
                        job_id,
                        progress=progress,
                        status_description=(
                            f'Regenerating page "{result.title}" '
                            f"({pages_done}/{total_pages})"
                        ),
                    )

            await generate_page_batch(
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
                on_progress=on_page_progress,
                on_result=_save_regenerated_page,
                plan=plan,
            )

        # Build a merged plan reflecting the full updated wiki structure.
        # Unchanged pages are identified by title, not slug, so a retitled page
        # in the new plan doesn't accidentally preserve the stale old entry.
        merged_pages = _merge_refresh_plan_pages(
            old_pages=old_plan.pages,
            replacement_pages=plan.pages,
            affected_titles=affected_page_titles,
        )
        merged_plan = WikiPlan(
            repo_notes=old_plan.repo_notes,
            pages=merged_pages,
            all_repo_files=sorted(new_all_files),
        )
        page_order_by_slug = {
            page_spec.slug: order for order, page_spec in enumerate(merged_pages)
        }

        async with get_session(db_path) as s:
            existing_result = await s.execute(
                sa_select(WikiPage).where(WikiPage.repo_id == repo_id)
            )
            for existing_page in existing_result.scalars().all():
                if existing_page.slug in page_order_by_slug:
                    existing_page.page_order = page_order_by_slug[existing_page.slug]

            await s.execute(
                sa_delete(WikiPage).where(
                    WikiPage.repo_id == repo_id, WikiPage.slug.in_(affected_old_slugs)
                )
            )
            for result, page_spec in staged_pages.values():
                s.add(
                    WikiPage(
                        id=str(uuid.uuid4()),
                        repo_id=repo_id,
                        slug=result.slug,
                        title=result.title,
                        content=result.content,
                        page_order=page_order_by_slug[result.slug],
                        parent_slug=page_spec.parent_slug,
                        description=page_spec.purpose,
                    )
                )
            await s.commit()

        def _remove_replaced_markdown() -> None:
            for slug in affected_old_slugs:
                path = wiki_dir / f"{slug}.md"
                if path.exists():
                    path.unlink()

        await asyncio.get_running_loop().run_in_executor(
            None, _remove_replaced_markdown
        )
        for result, _page_spec in staged_pages.values():
            await _write_text_async(wiki_dir / f"{result.slug}.md", result.content)

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
        await asyncio.get_running_loop().run_in_executor(
            None, _remove_stale_ast_artifacts, ast_dir
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
