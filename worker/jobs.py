from __future__ import annotations
import uuid
from datetime import datetime, timezone
from pathlib import Path

from shared.config import get_config
from shared.database import get_session, init_db
from shared.models import Repository, Job, WikiPage
from worker.pipeline.ingestion import filter_files, clone_or_fetch
from worker.pipeline.ast_analysis import build_module_tree
from worker.pipeline.rag_indexer import build_rag_index, FAISSStore
from worker.pipeline.wiki_planner import generate_page_plan
from worker.pipeline.page_generator import generate_page
from worker.llm import make_llm_provider
from worker.embedding import make_embedding_provider


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
        if clone_root is None:
            clone_root = data_dir / "repos" / repo_id / "clone"
        head_sha = await clone_or_fetch(clone_root, owner, name)
        files = filter_files(clone_root)
        await _update_job(db_path, job_id, progress=20)

        # Stage 2: AST Analysis
        module_tree = build_module_tree(clone_root, files)
        await _update_job(db_path, job_id, progress=35)

        # Stage 3: RAG Indexer
        llm = make_llm_provider(cfg)
        embedding = make_embedding_provider(cfg)
        repo_data_dir = data_dir / "repos" / repo_id
        repo_data_dir.mkdir(parents=True, exist_ok=True)
        store = FAISSStore(
            dimension=embedding.dimension,
            index_path=repo_data_dir / "faiss.index",
            meta_path=repo_data_dir / "faiss.meta.pkl",
        )
        await build_rag_index(files, clone_root, store, embedding)
        await _update_job(db_path, job_id, progress=55)

        # Stage 4: Wiki Planner
        plan = await generate_page_plan(module_tree, repo_name=name, llm=llm)
        await _update_job(db_path, job_id, progress=65)

        # Stage 5: Page Generator
        wiki_dir = repo_data_dir / "wiki"
        wiki_dir.mkdir(exist_ok=True)
        total = len(plan.pages)
        for i, page_spec in enumerate(plan.pages):
            result = await generate_page(page_spec, store, llm, embedding, repo_name=name)
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
                )
                s.add(page)
                await s.commit()
            progress = 65 + int(35 * (i + 1) / total)
            await _update_job(db_path, job_id, progress=progress)

        # Done
        now = datetime.now(timezone.utc)
        await _update_job(db_path, job_id, status="done", progress=100, finished_at=now)
        await _update_repo(db_path, repo_id, status="ready", last_commit=head_sha,
                           indexed_at=now, wiki_path=str(wiki_dir))

    except Exception as e:
        now = datetime.now(timezone.utc)
        await _update_job(db_path, job_id, status="failed", error=str(e), finished_at=now)
        await _update_repo(db_path, repo_id, status="error")
        raise
