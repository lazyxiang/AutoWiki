from __future__ import annotations

import asyncio
import json
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

    # Retry callback — updates job status_description to show retry state
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

    try:
        await _update_job(
            db_path,
            job_id,
            status="running",
            progress=5,
            status_description="Cloning repository and fetching files...",
        )
        await _update_repo(db_path, repo_id, status="indexing")

        # Stage 1: Ingestion
        repo_data_dir = data_dir / "repos" / repo_id
        repo_data_dir.mkdir(parents=True, exist_ok=True)
        if clone_root is None:
            clone_root = repo_data_dir / "clone"
        head_sha = await clone_or_fetch(clone_root, owner, name)
        autowikiignore = clone_root / ".autowikiignore"
        files = filter_files(clone_root, ignore_file=autowikiignore)
        readme = extract_readme(clone_root)
        await _update_job(
            db_path,
            job_id,
            progress=20,
            status_description="Analyzing source code structure (AST)...",
        )

        # Stage 2: AST Analysis — enhanced module tree with entity summaries
        module_tree = build_module_tree(clone_root, files)
        enhanced_tree = build_enhanced_module_tree(clone_root, files)

        # Persist module tree for incremental refresh and graph API
        ast_dir = repo_data_dir / "ast"
        ast_dir.mkdir(parents=True, exist_ok=True)
        (ast_dir / "module_tree.json").write_text(json.dumps(module_tree))

        # Build per-file entity map for entity-aware RAG chunking
        file_entities: dict[str, list[dict]] = {}
        for f in files:
            analysis = analyze_file(f)
            if analysis and analysis["entities"]:
                try:
                    rel = str(f.relative_to(clone_root))
                except ValueError:
                    rel = str(f)
                file_entities[rel] = analysis["entities"]
        await _update_job(
            db_path,
            job_id,
            progress=30,
            status_description="Building dependency graph...",
        )

        # Stage 2b: Dependency Graph
        dep_graph = build_dependency_graph(files, clone_root)
        module_files: dict[str, list[str]] = {}
        for m in module_tree:
            try:
                module_files[m["path"]] = [
                    str(Path(f).relative_to(clone_root)) for f in m["files"]
                ]
            except (ValueError, TypeError):
                module_files[m["path"]] = m["files"]
        dep_summary = summarize_dependencies(dep_graph, module_files)

        # Stage 3: RAG Indexer — entity-aware chunking with line numbers
        llm = make_llm_provider(cfg)
        embedding = make_embedding_provider(cfg)

        index_path = repo_data_dir / "faiss.index"
        meta_path = repo_data_dir / "faiss.meta.pkl"
        wiki_dir = repo_data_dir / "wiki"

        # Force mode: clear all previously generated artifacts
        if force:

            def _remove_artifacts() -> None:
                for p in (index_path, meta_path):
                    if p.exists():
                        p.unlink()
                if wiki_dir.exists():
                    for f in wiki_dir.glob("*.md"):
                        f.unlink()

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _remove_artifacts)
            async with get_session(db_path) as s:
                await s.execute(sa_delete(WikiPage).where(WikiPage.repo_id == repo_id))
                await s.commit()

        store = FAISSStore(
            dimension=embedding.dimension,
            index_path=index_path,
            meta_path=meta_path,
        )

        if index_path.exists() and meta_path.exists():
            await _update_job(
                db_path,
                job_id,
                progress=55,
                status_description="Using existing code index...",
            )
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, store.load)
        else:
            await _update_job(
                db_path,
                job_id,
                progress=40,
                status_description="Indexing code for RAG search (embedding)...",
            )
            await build_rag_index(
                files,
                clone_root,
                store,
                embedding,
                file_entities=file_entities,
                on_retry=_on_retry,
            )
            await _update_job(
                db_path,
                job_id,
                progress=55,
                status_description="Planning wiki structure...",
            )

        # Stage 4: Wiki Planner — enriched with README, deps, entities
        plan = await generate_page_plan(
            enhanced_tree,
            repo_name=name,
            llm=llm,
            readme=readme,
            dep_summary=dep_summary,
            clusters=dep_graph.clusters,
            on_retry=_on_retry,
        )
        await _update_job(db_path, job_id, progress=65)

        # Stage 5: Page Generator — with dependency context and entity details
        wiki_dir.mkdir(exist_ok=True)

        # Save wiki structure JSON
        structure_data = asdict(plan)
        (wiki_dir / "wiki.json").write_text(json.dumps(structure_data, indent=2))

        total = len(plan.pages)

        # Build entity details lookup per module for page generation
        module_entity_map: dict[str, list[dict]] = {}
        for m in enhanced_tree:
            mod_path = m["path"]
            entities_for_mod = []
            for cls in m.get("classes", []):
                entities_for_mod.append({**cls, "type": "class"})
            for fn in m.get("functions", []):
                entities_for_mod.append({**fn, "type": "function"})
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
            module_entity_map[mod_path] = entities_for_mod

        # Resume mode: load slugs of already-generated pages to skip them
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

            page_entities: list[dict] = []
            page_dep_info: dict = {
                "depends_on": [],
                "depended_by": [],
                "external_deps": [],
            }
            for mod in page_spec.modules:
                page_entities.extend(module_entity_map.get(mod, []))
                mod_dep = dep_summary.get(mod, {})
                for key in ("depends_on", "depended_by", "external_deps"):
                    page_dep_info[key] = list(
                        set(page_dep_info[key]) | set(mod_dep.get(key, []))
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
            async with get_session(db_path) as s:
                page = WikiPage(
                    id=str(uuid.uuid4()),
                    repo_id=repo_id,
                    slug=result.slug,
                    title=result.title,
                    content=result.content,
                    page_order=i,
                    parent_slug=page_spec.parent_slug,
                    description=page_spec.description,
                )
                s.add(page)
                await s.commit()
            (wiki_dir / f"{result.slug}.md").write_text(result.content)
            await _update_job(
                db_path,
                job_id,
                progress=progress,
                status_description=f"Generating page: {result.title}...",
            )

        # Stage 6: Architecture Diagram Synthesis
        diagram = await synthesize_diagrams(module_tree, repo_name=name, llm=llm)
        if diagram is not None and plan.pages:
            first_slug = plan.pages[0].slug
            async with get_session(db_path) as s:
                result_row = await s.execute(
                    sa_select(WikiPage).where(
                        WikiPage.repo_id == repo_id,
                        WikiPage.slug == first_slug,
                    )
                )
                first_page = result_row.scalar_one_or_none()
                if first_page is not None:
                    prefix = f"## Architecture\n\n```mermaid\n{diagram}\n```\n\n"
                    first_page.content = prefix + first_page.content
                    await s.commit()
                    wiki_file = wiki_dir / f"{first_page.slug}.md"
                    wiki_file.write_text(first_page.content)
            (ast_dir / "architecture.mmd").write_text(diagram)

        now = datetime.now(UTC)
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

    # Retry callback — updates job status_description to show retry state
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

    try:
        await _update_job(
            db_path,
            job_id,
            status="running",
            progress=5,
            status_description="Fetching latest commits...",
        )

        # Stage 1: Clone/fetch to get new HEAD
        repo_data_dir = data_dir / "repos" / repo_id
        if clone_root is None:
            clone_root = repo_data_dir / "clone"
        new_sha = await clone_or_fetch(clone_root, owner, name)

        # Check if anything changed
        async with get_session(db_path) as s:
            repo = await s.get(Repository, repo_id)
            old_sha = repo.last_commit or ""

        if old_sha == new_sha:
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

        # Find changed files and affected modules
        changed_files = (
            await get_changed_files(clone_root, old_sha, new_sha) if old_sha else []
        )
        ast_dir = repo_data_dir / "ast"
        module_tree_path = ast_dir / "module_tree.json"
        if not module_tree_path.exists():
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

        old_module_tree = json.loads(module_tree_path.read_text())
        affected_modules = get_affected_modules(changed_files, old_module_tree)
        if not affected_modules:
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

        await _update_job(
            db_path,
            job_id,
            progress=20,
            status_description="Analyzing updated source code...",
        )

        # Stage 2: Re-analyze AST with full quality enhancements
        autowikiignore = clone_root / ".autowikiignore"
        files = filter_files(clone_root, ignore_file=autowikiignore)
        readme = extract_readme(clone_root)
        module_tree = build_module_tree(clone_root, files)
        enhanced_tree = build_enhanced_module_tree(clone_root, files)

        ast_dir.mkdir(parents=True, exist_ok=True)
        (ast_dir / "module_tree.json").write_text(json.dumps(module_tree))

        # Detect structural changes: added or removed top-level modules
        old_module_paths = {m["path"] for m in old_module_tree}
        new_module_paths = {m["path"] for m in module_tree}
        added_modules = new_module_paths - old_module_paths
        removed_modules = old_module_paths - new_module_paths

        # New modules: add to affected set so they get generated
        affected_modules = affected_modules | added_modules

        # Removed modules: we have no module→page mapping to selectively clean up,
        # so fall back to a full force reindex which clears stale pages
        if removed_modules:
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

        file_entities: dict[str, list[dict]] = {}
        for f in files:
            analysis = analyze_file(f)
            if analysis and analysis["entities"]:
                try:
                    rel = str(f.relative_to(clone_root))
                except ValueError:
                    rel = str(f)
                file_entities[rel] = analysis["entities"]
        await _update_job(
            db_path,
            job_id,
            progress=30,
            status_description="Rebuilding dependency graph...",
        )

        # Stage 2b: Dependency Graph
        dep_graph = build_dependency_graph(files, clone_root)
        module_files: dict[str, list[str]] = {}
        for m in module_tree:
            try:
                module_files[m["path"]] = [
                    str(Path(f).relative_to(clone_root)) for f in m["files"]
                ]
            except (ValueError, TypeError):
                module_files[m["path"]] = m["files"]
        dep_summary = summarize_dependencies(dep_graph, module_files)
        await _update_job(
            db_path,
            job_id,
            progress=40,
            status_description="Rebuilding RAG index...",
        )

        # Stage 3: Rebuild FAISS index
        llm = make_llm_provider(cfg)
        embedding = make_embedding_provider(cfg)
        repo_data_dir.mkdir(parents=True, exist_ok=True)
        store = FAISSStore(
            dimension=embedding.dimension,
            index_path=repo_data_dir / "faiss.index",
            meta_path=repo_data_dir / "faiss.meta.pkl",
        )
        await build_rag_index(
            files,
            clone_root,
            store,
            embedding,
            file_entities=file_entities,
            on_retry=_on_retry,
        )
        await _update_job(
            db_path,
            job_id,
            progress=55,
            status_description="Re-planning updated wiki pages...",
        )

        # Stage 4: Re-plan for affected modules with quality enrichment
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
                    WikiPage.repo_id == repo_id,
                    WikiPage.slug.in_(new_slugs),
                )
            )
            await s.commit()

        # Stage 5: Regenerate pages with quality enrichment
        wiki_dir = repo_data_dir / "wiki"
        wiki_dir.mkdir(exist_ok=True)
        total = len(plan.pages)

        module_entity_map: dict[str, list[dict]] = {}
        for m in enhanced_tree:
            mod_path = m["path"]
            entities_for_mod = []
            for cls in m.get("classes", []):
                entities_for_mod.append({**cls, "type": "class"})
            for fn in m.get("functions", []):
                entities_for_mod.append({**fn, "type": "function"})
            module_entity_map[mod_path] = entities_for_mod

        for i, page_spec in enumerate(plan.pages):
            page_entities: list[dict] = []
            page_dep_info: dict = {
                "depends_on": [],
                "depended_by": [],
                "external_deps": [],
            }
            for mod in page_spec.modules:
                page_entities.extend(module_entity_map.get(mod, []))
                mod_dep = dep_summary.get(mod, {})
                for key in ("depends_on", "depended_by", "external_deps"):
                    page_dep_info[key] = list(
                        set(page_dep_info[key]) | set(mod_dep.get(key, []))
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
            # Preserve original page_order for replaced pages; append truly new ones
            page_order = old_page_orders.get(result.slug, max_existing_order + 1 + i)
            async with get_session(db_path) as s:
                page = WikiPage(
                    id=str(uuid.uuid4()),
                    repo_id=repo_id,
                    slug=result.slug,
                    title=result.title,
                    content=result.content,
                    page_order=page_order,
                    parent_slug=page_spec.parent_slug,
                    description=page_spec.description,
                )
                s.add(page)
                await s.commit()
            (wiki_dir / f"{result.slug}.md").write_text(result.content)
            progress = 65 + int(30 * (i + 1) / total) if total > 0 else 95
            await _update_job(
                db_path,
                job_id,
                progress=progress,
                status_description=f"Regenerating page: {result.title}...",
            )

        # Stage 6: Rebuild architecture diagram and update first wiki page
        diagram = await synthesize_diagrams(module_tree, repo_name=name, llm=llm)
        if diagram:
            (ast_dir / "architecture.mmd").write_text(diagram)
            async with get_session(db_path) as s:
                result_row = await s.execute(
                    sa_select(WikiPage)
                    .where(WikiPage.repo_id == repo_id)
                    .order_by(WikiPage.page_order)
                    .limit(1)
                )
                first_page = result_row.scalar_one_or_none()
                if first_page is not None:
                    prefix = f"## Architecture\n\n```mermaid\n{diagram}\n```\n\n"
                    # Replace existing diagram section if present, else prepend.
                    # Use regex to strip only the architecture block so content
                    # with blank lines inside the mermaid fence is handled correctly.
                    if first_page.content.startswith("## Architecture"):
                        stripped = re.sub(
                            r"^## Architecture\n\n```mermaid\n.*?```\n\n",
                            "",
                            first_page.content,
                            count=1,
                            flags=re.DOTALL,
                        )
                        first_page.content = prefix + stripped
                    else:
                        first_page.content = prefix + first_page.content
                    await s.commit()
                    wiki_dir = repo_data_dir / "wiki"
                    wiki_dir.mkdir(parents=True, exist_ok=True)
                    (wiki_dir / f"{first_page.slug}.md").write_text(first_page.content)

        now = datetime.now(UTC)
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
        )

    except Exception as e:
        now = datetime.now(UTC)
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
