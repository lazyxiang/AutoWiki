from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete as sa_delete
from sqlalchemy import select as sa_select

from shared.config import get_config
from shared.database import get_session, init_db
from shared.models import Job, Repository, WikiPage
from worker.embedding import make_embedding_provider
from worker.llm import make_llm_provider
from worker.pipeline.ast_analysis import (
    analyze_file,
    build_enhanced_module_tree,
    build_module_tree,
)
from worker.pipeline.dependency_graph import (
    build_dependency_graph,
    summarize_dependencies,
)
from worker.pipeline.diagram_synthesis import synthesize_diagrams
from worker.pipeline.ingestion import (
    clone_or_fetch,
    extract_readme,
    filter_files,
    get_affected_modules,
    get_changed_files,
)
from worker.pipeline.page_generator import generate_page
from worker.pipeline.rag_indexer import FAISSStore, build_rag_index
from worker.pipeline.wiki_planner import generate_page_plan

logger = logging.getLogger("worker.task")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _update_job(db_path: str, job_id: str, **kwargs):
    async with get_session(db_path) as s:
        job = await s.get(Job, job_id)
        for k, v in kwargs.items():
            setattr(job, k, v)
        await s.commit()


async def _update_repo(db_path: str, repo_id: str, **kwargs):
    async with get_session(db_path) as s:
        repo = await s.get(Repository, repo_id)
        for k, v in kwargs.items():
            setattr(repo, k, v)
        await s.commit()


async def _write_text_async(path: Path, content: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, path.write_text, content)


# ---------------------------------------------------------------------------
# Pipeline stage helpers (shared by full-index and refresh)
# ---------------------------------------------------------------------------


def _make_on_retry(db_path: str, job_id: str):
    """Return an on_retry callback that updates the job's status_description."""

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


def _build_file_entities(files: list[Path], clone_root: Path) -> dict[str, list[dict]]:
    """Run AST analysis on each file and return a map of rel_path → entities."""
    file_entities: dict[str, list[dict]] = {}
    for f in files:
        analysis = analyze_file(f)
        if analysis and analysis["entities"]:
            try:
                rel = str(f.relative_to(clone_root))
            except ValueError:
                rel = str(f)
            file_entities[rel] = analysis["entities"]
    return file_entities


def _build_module_files(
    module_tree: list[dict], clone_root: Path
) -> dict[str, list[str]]:
    """Return a map of module_path → list of relative file paths."""
    module_files: dict[str, list[str]] = {}
    for m in module_tree:
        try:
            module_files[m["path"]] = [
                str(Path(f).relative_to(clone_root)) for f in m["files"]
            ]
        except (ValueError, TypeError):
            module_files[m["path"]] = m["files"]
    return module_files


def _build_module_entity_map(
    enhanced_tree: list[dict], file_entities: dict[str, list[dict]]
) -> dict[str, list[dict]]:
    """Build a lookup of module_path → enriched entity list for page generation."""
    module_entity_map: dict[str, list[dict]] = {}
    for m in enhanced_tree:
        mod_path = m["path"]
        entities_for_mod = []
        for cls in m.get("classes", []):
            entities_for_mod.append({**cls, "type": "class"})
        for fn in m.get("functions", []):
            entities_for_mod.append({**fn, "type": "function"})
        # Enrich each entity with its source file and line numbers from file_entities
        for e in entities_for_mod:
            if "file" not in e:
                for f_path in m.get("files", []):
                    for rel_path, ents in file_entities.items():
                        if rel_path.endswith(f_path) or f_path.endswith(rel_path):
                            for fe in ents:
                                if fe.get("name") == e.get("name"):
                                    e["file"] = rel_path
                                    e["start_line"] = fe.get("start_line")
                                    e["end_line"] = fe.get("end_line")
                                    break
                            if "file" in e:
                                break
                    if "file" in e:
                        break
        # Append file-level entities not captured by the enhanced_tree summary
        # (e.g. entities beyond the 10-class/15-function cap or nested members)
        seen_names = {e.get("name") for e in entities_for_mod}
        for f_path in m.get("files", []):
            for rel_path, ents in file_entities.items():
                if rel_path.endswith(f_path) or f_path.endswith(rel_path):
                    for fe in ents:
                        if fe.get("name") not in seen_names:
                            entities_for_mod.append({**fe, "file": rel_path})
                            seen_names.add(fe.get("name"))
        module_entity_map[mod_path] = entities_for_mod
    return module_entity_map


def _collect_page_context(
    page_spec,
    module_entity_map: dict[str, list[dict]],
    dep_summary: dict,
) -> tuple[list[dict], dict]:
    """Collect entity details and dependency info for a single page spec."""
    page_entities: list[dict] = []
    page_dep_info: dict = {"depends_on": [], "depended_by": [], "external_deps": []}
    for mod in page_spec.modules:
        page_entities.extend(module_entity_map.get(mod, []))
        mod_dep = dep_summary.get(mod, {})
        for key in ("depends_on", "depended_by", "external_deps"):
            page_dep_info[key] = list(
                set(page_dep_info[key]) | set(mod_dep.get(key, []))
            )
    return page_entities, page_dep_info


def _prepend_architecture_diagram(content: str, diagram: str) -> str:
    """Prepend (or replace) the Architecture mermaid block at the top of a page."""
    prefix = f"## Architecture\n\n```mermaid\n{diagram}\n```\n\n"
    stripped = re.sub(
        r"^## Architecture\s*\n+```mermaid\n.*?```\s*\n*",
        "",
        content,
        count=1,
        flags=re.DOTALL,
    )
    return prefix + stripped


def _make_faiss_store(repo_data_dir: Path, embedding) -> FAISSStore:
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
    force: bool = False,
):
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

        # Stage 1: Ingestion
        logger.info("Stage 1: Ingestion starting for %s/%s", owner, name)
        repo_data_dir = data_dir / "repos" / repo_id
        repo_data_dir.mkdir(parents=True, exist_ok=True)
        if clone_root is None:
            clone_root = repo_data_dir / "clone"
        head_sha = await clone_or_fetch(clone_root, owner, name)
        logger.info("Clone complete. HEAD SHA: %s", head_sha)
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

        # Stage 2: AST Analysis
        logger.info("Stage 2: AST Analysis starting")
        ast_dir = repo_data_dir / "ast"
        ast_dir.mkdir(parents=True, exist_ok=True)
        module_tree = build_module_tree(clone_root, files)
        logger.info("Module tree built: %d modules", len(module_tree))
        enhanced_tree = build_enhanced_module_tree(clone_root, files)
        logger.info("Enhanced tree built: %d modules", len(enhanced_tree))
        await _write_text_async(ast_dir / "module_tree.json", json.dumps(module_tree))
        file_entities = await loop.run_in_executor(
            None, _build_file_entities, files, clone_root
        )
        logger.info(
            "File entities analyzed: found entities in %d files", len(file_entities)
        )
        await _update_job(
            db_path,
            job_id,
            progress=30,
            status_description="Building dependency graph...",
        )

        # Stage 2b: Dependency Graph
        logger.info("Stage 2b: Dependency Graph starting")
        dep_graph = build_dependency_graph(files, clone_root)
        logger.info(
            "Dependency graph built: %d nodes, %d edges",
            sum(len(c) for c in dep_graph.clusters),
            sum(len(e) for e in dep_graph.edges.values()),
        )
        dep_summary = summarize_dependencies(
            dep_graph, _build_module_files(module_tree, clone_root)
        )
        logger.info(
            "Dependency summary created: summarized %d modules", len(dep_summary)
        )

        # Stage 3: RAG Indexer
        logger.info("Stage 3: RAG Indexer starting")
        llm = make_llm_provider(cfg)
        embedding = make_embedding_provider(cfg)
        logger.info(
            "Using embedding provider: %s, model: %s (dim=%d)",
            cfg.embedding.provider,
            cfg.embedding.model,
            embedding.dimension,
        )

        index_path = repo_data_dir / "faiss.index"
        meta_path = repo_data_dir / "faiss.meta.pkl"
        wiki_dir = repo_data_dir / "wiki"

        if force:

            def _remove_artifacts() -> None:
                for p in (index_path, meta_path):
                    if p.exists():
                        p.unlink()
                if wiki_dir.exists():
                    for f in wiki_dir.glob("*.md"):
                        f.unlink()

            await asyncio.get_running_loop().run_in_executor(None, _remove_artifacts)
            async with get_session(db_path) as s:
                await s.execute(sa_delete(WikiPage).where(WikiPage.repo_id == repo_id))
                await s.commit()

        store = _make_faiss_store(repo_data_dir, embedding)
        if index_path.exists() and meta_path.exists():
            await _update_job(
                db_path,
                job_id,
                progress=55,
                status_description="Using existing code index...",
            )
            logger.info("Loading existing RAG index from %s", index_path)
            await asyncio.get_running_loop().run_in_executor(None, store.load)
        else:
            await _update_job(
                db_path,
                job_id,
                progress=40,
                status_description="Indexing code for RAG search (embedding)...",
            )
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

        # Stage 4: Wiki Planner
        logger.info("Stage 4: Wiki Planner starting")
        plan = await generate_page_plan(
            enhanced_tree,
            repo_name=name,
            llm=llm,
            readme=readme,
            dep_summary=dep_summary,
            clusters=dep_graph.clusters,
            on_retry=_on_retry,
        )
        logger.info(
            "Wiki plan generated: %d pages planned for %s", len(plan.pages), name
        )
        await _update_job(db_path, job_id, progress=65)

        # Stage 5: Page Generator
        logger.info("Stage 5: Page Generator starting")
        wiki_dir.mkdir(exist_ok=True)
        structure_data = asdict(plan)
        await _write_text_async(
            wiki_dir / "wiki.json", json.dumps(structure_data, indent=2)
        )
        total = len(plan.pages)
        module_entity_map = _build_module_entity_map(enhanced_tree, file_entities)

        existing_slugs: set[str] = set()
        if not force:
            async with get_session(db_path) as s:
                result = await s.execute(
                    sa_select(WikiPage).where(WikiPage.repo_id == repo_id)
                )
                existing_slugs = {p.slug for p in result.scalars().all()}

        for i, page_spec in enumerate(plan.pages):
            progress = 65 + int(30 * (i + 1) / total)
            if page_spec.slug in existing_slugs:
                await _update_job(
                    db_path,
                    job_id,
                    progress=progress,
                    status_description=f"Skipping existing page: {page_spec.title}",
                )
                continue
            page_entities, page_dep_info = _collect_page_context(
                page_spec, module_entity_map, dep_summary
            )
            result = await generate_page(
                page_spec,
                store,
                llm,
                embedding,
                repo_name=name,
                dep_info=page_dep_info if any(page_dep_info.values()) else None,
                entity_details=page_entities if page_entities else None,
                on_retry=_on_retry,
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
                        description=page_spec.description,
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

        # Stage 6: Architecture Diagram Synthesis
        logger.info("Stage 6: Architecture Diagram Synthesis starting")
        diagram = await synthesize_diagrams(module_tree, repo_name=name, llm=llm)
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
):
    """Incremental refresh: re-run pipeline only for modules with changed files."""
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
        new_sha = await clone_or_fetch(clone_root, owner, name)
        logger.info("Fetch complete. New HEAD SHA: %s", new_sha)

        async with get_session(db_path) as s:
            repo = await s.get(Repository, repo_id)
            old_sha = repo.last_commit or ""

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

        # Find changed files and affected modules.
        # Falls back to a forced full reindex if the stored SHA is unreachable
        # (e.g. shallow clone that no longer contains the base commit).
        try:
            changed_files = (
                await get_changed_files(clone_root, old_sha, new_sha) if old_sha else []
            )
            logger.info("Changed files detected: %d files", len(changed_files))
        except Exception:
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
                force=True,
            )
            return

        ast_dir = repo_data_dir / "ast"
        module_tree_path = ast_dir / "module_tree.json"
        if not module_tree_path.exists():
            logger.info("No existing module tree found. Falling back to full reindex.")
            # No prior index — force a clean full reindex to avoid duplicate pages
            await run_full_index(
                ctx,
                repo_id=repo_id,
                job_id=job_id,
                owner=owner,
                name=name,
                clone_root=clone_root,
                force=True,
            )
            return

        content = await asyncio.get_running_loop().run_in_executor(
            None, module_tree_path.read_text
        )
        old_module_tree = json.loads(content)
        affected_modules = get_affected_modules(changed_files, old_module_tree)
        if not affected_modules:
            logger.info("No affected modules found for changed files.")
            now = datetime.now(UTC)
            await _update_repo(db_path, repo_id, last_commit=new_sha, status="ready")
            await _update_job(
                db_path,
                job_id,
                status="done",
                progress=100,
                finished_at=now,
                status_description="No affected modules found.",
            )
            return

        logger.info("Affected modules: %s", ", ".join(affected_modules))
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
        module_tree = build_module_tree(clone_root, files)
        logger.info("Module tree built: %d modules", len(module_tree))
        enhanced_tree = build_enhanced_module_tree(clone_root, files)
        logger.info("Enhanced tree built: %d modules", len(enhanced_tree))
        ast_dir.mkdir(parents=True, exist_ok=True)
        await _write_text_async(ast_dir / "module_tree.json", json.dumps(module_tree))

        # Detect structural changes: added or removed top-level modules
        old_module_paths = {m["path"] for m in old_module_tree}
        new_module_paths = {m["path"] for m in module_tree}
        added_modules = new_module_paths - old_module_paths
        removed_modules = old_module_paths - new_module_paths

        if added_modules:
            logger.info("Added modules detected: %s", ", ".join(added_modules))
            affected_modules = affected_modules | added_modules

        if removed_modules:
            logger.info(
                "Removed modules detected (%s). Falling back to full reindex.",
                ", ".join(removed_modules),
            )
            await run_full_index(
                ctx,
                repo_id=repo_id,
                job_id=job_id,
                owner=owner,
                name=name,
                clone_root=clone_root,
                force=True,
            )
            return

        file_entities = await loop.run_in_executor(
            None, _build_file_entities, files, clone_root
        )
        logger.info(
            "File entities analyzed: found entities in %d files", len(file_entities)
        )
        await _update_job(
            db_path,
            job_id,
            progress=30,
            status_description="Rebuilding dependency graph...",
        )

        # Stage 2b: Dependency Graph
        logger.info("Stage 2b: Dependency Graph starting")
        dep_graph = build_dependency_graph(files, clone_root)
        logger.info(
            "Dependency graph built: %d nodes, %d edges",
            sum(len(c) for c in dep_graph.clusters),
            sum(len(e) for e in dep_graph.edges.values()),
        )
        dep_summary = summarize_dependencies(
            dep_graph, _build_module_files(module_tree, clone_root)
        )
        logger.info(
            "Dependency summary created: summarized %d modules", len(dep_summary)
        )
        await _update_job(
            db_path, job_id, progress=40, status_description="Rebuilding RAG index..."
        )

        # Stage 3: Rebuild FAISS index
        logger.info("Stage 3: RAG Indexer starting")
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

        # Stage 4: Re-plan for affected modules
        logger.info(
            "Stage 4: Wiki Planner starting for %d affected modules",
            len(affected_modules),
        )
        affected_enhanced = [m for m in enhanced_tree if m["path"] in affected_modules]
        plan = await generate_page_plan(
            affected_enhanced,
            repo_name=name,
            llm=llm,
            readme=readme,
            dep_summary=dep_summary,
            clusters=dep_graph.clusters,
            on_retry=_on_retry,
        )
        logger.info(
            "Wiki plan generated: %d pages updated for %s", len(plan.pages), name
        )
        await _update_job(db_path, job_id, progress=65)

        # Capture existing page_orders before deletion to preserve stable ordering
        new_slugs = {p.slug for p in plan.pages}
        old_page_orders: dict[str, int] = {}
        max_existing_order = 0
        async with get_session(db_path) as s:
            result = await s.execute(
                sa_select(WikiPage).where(WikiPage.repo_id == repo_id)
            )
            for p in result.scalars().all():
                if p.slug in new_slugs:
                    old_page_orders[p.slug] = p.page_order
                max_existing_order = max(max_existing_order, p.page_order)

        async with get_session(db_path) as s:
            await s.execute(
                sa_delete(WikiPage).where(
                    WikiPage.repo_id == repo_id, WikiPage.slug.in_(new_slugs)
                )
            )
            await s.commit()

        # Stage 5: Regenerate pages
        logger.info("Stage 5: Page Generator starting")
        wiki_dir = repo_data_dir / "wiki"
        wiki_dir.mkdir(exist_ok=True)
        total = len(plan.pages)
        module_entity_map = _build_module_entity_map(enhanced_tree, file_entities)

        for i, page_spec in enumerate(plan.pages):
            page_entities, page_dep_info = _collect_page_context(
                page_spec, module_entity_map, dep_summary
            )
            result = await generate_page(
                page_spec,
                store,
                llm,
                embedding,
                repo_name=name,
                dep_info=page_dep_info if any(page_dep_info.values()) else None,
                entity_details=page_entities if page_entities else None,
                on_retry=_on_retry,
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
                        description=page_spec.description,
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

        # Stage 6: Rebuild architecture diagram and update first wiki page
        logger.info("Stage 6: Architecture Diagram Synthesis starting")
        diagram = await synthesize_diagrams(module_tree, repo_name=name, llm=llm)
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

        # Rebuild and persist wiki structure so navigation stays consistent
        async with get_session(db_path) as s:
            result_all = await s.execute(
                sa_select(WikiPage)
                .where(WikiPage.repo_id == repo_id)
                .order_by(WikiPage.page_order)
            )
            all_pages = result_all.scalars().all()
        structure_data = {
            "pages": [
                {
                    "title": p.title,
                    "slug": p.slug,
                    "modules": [],
                    "parent_slug": p.parent_slug,
                    "description": p.description,
                }
                for p in all_pages
            ]
        }
        wiki_dir.mkdir(parents=True, exist_ok=True)
        await _write_text_async(
            wiki_dir / "wiki.json", json.dumps(structure_data, indent=2)
        )

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
