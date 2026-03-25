from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete as sa_delete, select as sa_select

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
):
    cfg = get_config()
    db_path = str(cfg.database_path)
    data_dir = cfg.data_dir
    await init_db(db_path)

    try:
        await _update_job(db_path, job_id, status="running", progress=5)
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
        await _update_job(db_path, job_id, progress=20)

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
        await _update_job(db_path, job_id, progress=30)

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
        await _update_job(db_path, job_id, progress=40)

        # Stage 3: RAG Indexer — entity-aware chunking with line numbers
        llm = make_llm_provider(cfg)
        embedding = make_embedding_provider(cfg)
        store = FAISSStore(
            dimension=embedding.dimension,
            index_path=repo_data_dir / "faiss.index",
            meta_path=repo_data_dir / "faiss.meta.pkl",
        )
        await build_rag_index(
            files, clone_root, store, embedding, file_entities=file_entities
        )
        await _update_job(db_path, job_id, progress=55)

        # Stage 4: Wiki Planner — enriched with README, deps, entities
        plan = await generate_page_plan(
            enhanced_tree,
            repo_name=name,
            llm=llm,
            readme=readme,
            dep_summary=dep_summary,
            clusters=dep_graph.clusters,
        )
        await _update_job(db_path, job_id, progress=65)

        # Stage 5: Page Generator — with dependency context and entity details
        wiki_dir = repo_data_dir / "wiki"
        wiki_dir.mkdir(exist_ok=True)
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
            progress = 65 + int(30 * (i + 1) / total)
            await _update_job(db_path, job_id, progress=progress)

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
        await _update_job(db_path, job_id, progress=100)

        now = datetime.now(UTC)
        await _update_job(db_path, job_id, status="done", progress=100, finished_at=now)
        await _update_repo(
            db_path,
            repo_id,
            status="ready",
            last_commit=head_sha,
            indexed_at=now,
            wiki_path=str(wiki_dir),
        )

    except Exception as e:
        now = datetime.now(UTC)
        await _update_job(
            db_path, job_id, status="failed", error=str(e), finished_at=now
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

    try:
        await _update_job(db_path, job_id, status="running", progress=5)

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
            await _update_job(db_path, job_id, status="done", progress=100, finished_at=now)
            return

        # Find changed files and affected modules
        changed_files = get_changed_files(clone_root, old_sha, new_sha) if old_sha else []
        ast_dir = repo_data_dir / "ast"
        module_tree_path = ast_dir / "module_tree.json"
        if not module_tree_path.exists():
            await run_full_index(
                ctx, repo_id=repo_id, job_id=job_id, owner=owner, name=name,
                clone_root=clone_root,
            )
            return

        module_tree = json.loads(module_tree_path.read_text())
        affected_modules = get_affected_modules(changed_files, module_tree)
        if not affected_modules:
            now = datetime.now(UTC)
            await _update_repo(db_path, repo_id, last_commit=new_sha)
            await _update_job(db_path, job_id, status="done", progress=100, finished_at=now)
            return

        await _update_job(db_path, job_id, progress=20)

        # Stage 2: Re-analyze AST with full quality enhancements
        autowikiignore = clone_root / ".autowikiignore"
        files = filter_files(clone_root, ignore_file=autowikiignore)
        readme = extract_readme(clone_root)
        module_tree = build_module_tree(clone_root, files)
        enhanced_tree = build_enhanced_module_tree(clone_root, files)

        ast_dir.mkdir(parents=True, exist_ok=True)
        (ast_dir / "module_tree.json").write_text(json.dumps(module_tree))

        file_entities: dict[str, list[dict]] = {}
        for f in files:
            analysis = analyze_file(f)
            if analysis and analysis["entities"]:
                try:
                    rel = str(f.relative_to(clone_root))
                except ValueError:
                    rel = str(f)
                file_entities[rel] = analysis["entities"]
        await _update_job(db_path, job_id, progress=30)

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
        await _update_job(db_path, job_id, progress=40)

        # Stage 3: Rebuild FAISS index
        llm = make_llm_provider(cfg)
        embedding = make_embedding_provider(cfg)
        repo_data_dir.mkdir(parents=True, exist_ok=True)
        store = FAISSStore(
            dimension=embedding.dimension,
            index_path=repo_data_dir / "faiss.index",
            meta_path=repo_data_dir / "faiss.meta.pkl",
        )
        await build_rag_index(files, clone_root, store, embedding, file_entities=file_entities)
        await _update_job(db_path, job_id, progress=55)

        # Stage 4: Re-plan for affected modules with quality enrichment
        affected_enhanced = [m for m in enhanced_tree if m["path"] in affected_modules]
        plan = await generate_page_plan(
            affected_enhanced,
            repo_name=name,
            llm=llm,
            readme=readme,
            dep_summary=dep_summary,
            clusters=dep_graph.clusters,
        )
        await _update_job(db_path, job_id, progress=65)

        # Delete old pages whose slugs will be replaced
        new_slugs = {p.slug for p in plan.pages}
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
            page_dep_info: dict = {"depends_on": [], "depended_by": [], "external_deps": []}
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
            progress = 65 + int(30 * (i + 1) / total) if total > 0 else 95
            await _update_job(db_path, job_id, progress=progress)

        # Stage 6: Rebuild architecture diagram
        diagram = await synthesize_diagrams(module_tree, repo_name=name, llm=llm)
        if diagram:
            (ast_dir / "architecture.mmd").write_text(diagram)

        now = datetime.now(UTC)
        await _update_job(db_path, job_id, status="done", progress=100, finished_at=now)
        await _update_repo(
            db_path, repo_id, status="ready", last_commit=new_sha, indexed_at=now,
        )

    except Exception as e:
        now = datetime.now(UTC)
        await _update_job(db_path, job_id, status="failed", error=str(e), finished_at=now)
        await _update_repo(db_path, repo_id, status="error")
        raise
