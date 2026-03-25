import json

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from shared.config import get_config
from shared.database import get_session
from shared.models import Repository, WikiPage

router = APIRouter(prefix="/api/repos/{repo_id}/wiki")


@router.get("")
async def list_wiki_pages(repo_id: str):
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        repo = await s.get(Repository, repo_id)
        if repo and repo.wiki_structure:
            structure = json.loads(repo.wiki_structure)
            return {"pages": structure.get("pages", [])}

        result = await s.execute(
            select(WikiPage)
            .where(WikiPage.repo_id == repo_id)
            .order_by(WikiPage.page_order)
        )
        pages = result.scalars().all()
    return {
        "pages": [
            {
                "slug": p.slug,
                "title": p.title,
                "parent_slug": p.parent_slug,
                "page_order": p.page_order,
            }
            for p in pages
        ]
    }


@router.get("/{slug}")
async def get_wiki_page(repo_id: str, slug: str):
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        result = await s.execute(
            select(WikiPage)
            .where(WikiPage.repo_id == repo_id, WikiPage.slug == slug)
            .order_by(WikiPage.updated_at.desc())
        )
        page = result.scalars().first()
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return {
        "slug": page.slug,
        "title": page.title,
        "content": page.content,
        "parent_slug": page.parent_slug,
        "updated_at": page.updated_at,
    }
