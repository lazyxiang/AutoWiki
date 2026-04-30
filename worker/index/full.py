"""Full-index ARQ job orchestration for the 6-stage wiki pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete as sa_delete

from shared.config import get_config
from shared.database import get_session, init_db
from shared.models import Repository, WikiPage
from worker.embedding import make_embedding_provider
from worker.index.artifacts import (
    _make_faiss_store,
    _remove_path,
    _write_text_async,
)
from worker.index.backup import (
    _cleanup_first_time_index_failure,
    _discard_full_index_backup,
    _FullIndexBackup,
    _repo_has_previous_success,
    _restore_full_index_state,
    _snapshot_full_index_state,
)
from worker.index.progress import (
    _make_on_retry,
    _make_page_progress_callback,
    _update_job,
    _update_repo,
)
from worker.llm import make_fast_llm_provider, make_llm_provider
from worker.pipeline.ast_analysis import analyze_all_files
from worker.pipeline.dependency_graph import build_dependency_graph
from worker.pipeline.fast_report_index import build_fast_report_index
from worker.pipeline.ingestion import (
    clone_or_fetch,
    extract_readme,
    filter_files,
)
from worker.pipeline.page_generator import (
    PageResult,
    compute_generation_order,
    generate_page_batch,
)
from worker.pipeline.rag_indexer import build_rag_index
from worker.pipeline.user_steering import load_user_steering
from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan, generate_wiki_plan
from worker.platform.registry import get_platform_by_name
from worker.platform.token_store import get_platform_token

logger = logging.getLogger("worker.task")


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

    This is the primary ARQ job function.  For repositories with an existing
    successful wiki, it snapshots the artifacts that a full index may mutate,
    clears those artifacts to ensure a clean output, then executes each
    pipeline stage in sequence while writing progress to the DB.

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
            job status to ``"failed"`` before re-raising.  First-time failed
            repos are removed from repository metadata; previously indexed
            repos are restored to their prior wiki state and ``"ready"`` status.
    """
    cfg = get_config()
    db_path = str(cfg.database_path)
    data_dir = cfg.data_dir
    await init_db(db_path)
    _on_retry = _make_on_retry(db_path, job_id)
    repo_data_dir = data_dir / "repos" / repo_id
    repo_data_dir.mkdir(parents=True, exist_ok=True)
    full_index_backup: _FullIndexBackup | None = None
    previously_indexed = False

    try:
        logger.info("Job starting for %s/%s", owner, name)
        previously_indexed = await _repo_has_previous_success(
            db_path, repo_id, repo_data_dir
        )
        if previously_indexed:
            full_index_backup = await _snapshot_full_index_state(
                db_path,
                repo_id,
                repo_data_dir,
                job_id,
                reuse_index=reuse_index,
                reuse_plan=reuse_plan,
            )
        await _update_job(
            db_path,
            job_id,
            status="running",
            progress=5,
            status_description="Cloning repository and fetching files...",
        )
        await _update_repo(db_path, repo_id, status="indexing")

        # Clear all artifacts from any previous run before starting fresh.
        def _clear_repo_artifacts() -> None:
            """Remove generated wiki files and optionally the search index.

            Removes Markdown pages and other entries under ``wiki/``.  When
            *reuse_plan* is ``True`` the user-facing ``wiki.json`` is
            preserved so the reuse-plan branch can recover user-edited
            ``page_notes``; otherwise ``ast/wiki_plan.json`` is also deleted.
            When *reuse_index* is ``False`` the FAISS index and metadata
            pickle are deleted.  The git clone is preserved.
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
                    # Preserve wiki.json when reusing the plan: the reuse-plan
                    # path below reads it to recover user-edited page_notes.
                    if reuse_plan and f.name == "wiki.json":
                        continue
                    _remove_path(f)
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

        await _update_repo(
            db_path, repo_id, **_repo_metadata_updates(meta, active_branch)
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
            progress=10,
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
            progress=18,
            status_description="Building dependency graph...",
        )

        # Stage 3: Dependency Graph — file-level import graph; used for clustering
        logger.info("Stage 3: Dependency Graph starting")
        dep_graph = await loop.run_in_executor(
            None, build_dependency_graph, files, clone_root
        )
        logger.info(
            "Dependency graph built: %d nodes, %d edges",
            sum(len(c) for c in dep_graph.clusters),
            sum(len(e) for e in dep_graph.edges.values()),
        )
        fast_report_index = await loop.run_in_executor(
            None,
            lambda: build_fast_report_index(
                root=clone_root,
                files=files,
                file_analysis=file_analysis,
                dep_graph=dep_graph,
                readme=readme,
            ),
        )
        await _write_text_async(
            ast_dir / "fast_report_index.json",
            json.dumps(fast_report_index, indent=2, ensure_ascii=False),
        )
        await _update_job(
            db_path,
            job_id,
            progress=25,
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
            progress=35,
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
        # Always persist the plan: even when reusing an existing plan,
        # all_repo_files is rebuilt from the current analysis and must be
        # written so incremental refresh sees added/removed files.
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
            progress=50,
            status_description="Generating wiki pages...",
        )

        # Stage 6: Bottom-up page generation
        logger.info("Stage 6: Page Generator starting (bottom-up)")
        total = len(plan.pages)

        levels = compute_generation_order(plan)
        generated: dict[str, PageResult] = {}
        page_order_counter = 0
        pages_done = 0
        on_page_progress = _make_page_progress_callback(db_path, job_id, "Generating")
        save_lock = asyncio.Lock()

        for level in levels:
            specs_with_children: list[tuple[WikiPageSpec, list[PageResult] | None]] = []
            for page_spec in level:
                children = [
                    generated[p.slug]
                    for p in plan.pages
                    if p.parent == page_spec.title and p.slug in generated
                ]
                specs_with_children.append((page_spec, children or None))

            page_order_by_slug = {
                page_spec.slug: page_order_counter + idx
                for idx, (page_spec, _) in enumerate(specs_with_children)
            }
            page_order_counter += len(specs_with_children)

            async def _save_generated_page(
                result: PageResult, page_spec: WikiPageSpec
            ) -> None:
                nonlocal pages_done
                async with save_lock:
                    generated[result.slug] = result
                    logger.info(
                        "Page generated and persisted: %s (%s), %d chars",
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
                                page_order=page_order_by_slug[result.slug],
                                parent_slug=page_spec.parent_slug,
                                description=page_spec.purpose,
                            )
                        )
                        await s.commit()
                    await _write_text_async(
                        wiki_dir / f"{result.slug}.md", result.content
                    )
                    pages_done += 1
                    progress = 50 + int(48 * pages_done / total) if total > 0 else 98
                    await _update_job(
                        db_path,
                        job_id,
                        progress=progress,
                        status_description=(
                            f'Generating page "{result.title}" ({pages_done}/{total})'
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
                on_result=_save_generated_page,
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
        await _discard_full_index_backup(full_index_backup)

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
        if full_index_backup is not None:
            try:
                await _restore_full_index_state(db_path, repo_id, full_index_backup)
            except Exception:
                logger.exception(
                    "Failed to restore prior wiki state for %s/%s after index failure",
                    owner,
                    name,
                )
                # Restore failed — clear the "indexing" status set at start
                # of the job so the repo isn't stranded as non-refreshable.
                await _update_repo(db_path, repo_id, status="error")
            try:
                await _discard_full_index_backup(full_index_backup)
            except Exception:
                logger.exception(
                    "Failed to discard full-index backup for %s/%s after index failure",
                    owner,
                    name,
                )
        elif previously_indexed:
            await _update_repo(db_path, repo_id, status="ready")
        else:
            await _cleanup_first_time_index_failure(
                db_path,
                repo_id,
                repo_data_dir,
                current_job_id=job_id,
                reuse_index=reuse_index,
                reuse_plan=reuse_plan,
            )
        raise
