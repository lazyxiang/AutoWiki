from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select

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
from worker.pipeline.ingestion import clone_or_fetch, extract_readme, filter_files
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
        if clone_root is None:
            clone_root = data_dir / "repos" / repo_id / "clone"
        head_sha = await clone_or_fetch(clone_root, owner, name)
        files = filter_files(clone_root)
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
        await _update_job(
            db_path,
            job_id,
            progress=40,
            status_description="Indexing code for RAG search...",
        )

        # Stage 3: RAG Indexer — entity-aware chunking with line numbers
        llm = make_llm_provider(cfg)
        embedding = make_embedding_provider(cfg)
        repo_data_dir = data_dir / "repos" / repo_id
        repo_data_dir.mkdir(parents=True, exist_ok=True)

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
                await s.execute(delete(WikiPage).where(WikiPage.repo_id == repo_id))
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
            # Add file paths to entities
            for e in entities_for_mod:
                if "file" not in e:
                    # Find a file that contains this entity
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
                    select(WikiPage).where(WikiPage.repo_id == repo_id)
                )
                existing_slugs = {p.slug for p in result.scalars().all()}

        for i, page_spec in enumerate(plan.pages):
            progress = 65 + int(35 * (i + 1) / total)

            if page_spec.slug in existing_slugs:
                await _update_job(
                    db_path,
                    job_id,
                    progress=progress,
                    status_description=f"Skipping existing page: {page_spec.title}",
                )
                continue

            # Gather entity details for all modules in this page
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
            (wiki_dir / f"{result.slug}.md").write_text(result.content)
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
            await _update_job(
                db_path,
                job_id,
                progress=progress,
                status_description=f"Generating page: {result.title}...",
            )

        # Done
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
