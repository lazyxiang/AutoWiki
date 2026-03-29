"""REST endpoints for browsing and reading generated wiki pages.

Routes are mounted under ``/api/repos/{repo_id}/wiki``.  Two endpoints are
provided: one for listing all pages in a repository's wiki, and one for
fetching the full Markdown content of a single page by its slug.
"""

import json

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from shared.config import get_config
from shared.database import get_session
from shared.models import Repository, WikiPage

router = APIRouter(prefix="/api/repos/{repo_id}/wiki")


@router.get("")
async def list_wiki_pages(repo_id: str):
    """List all wiki pages for a repository.

    Uses a two-path strategy to return the page listing efficiently:

    1. **Fast path** — if the ``Repository`` row has a non-empty
       ``wiki_structure`` JSON column (populated at the end of the generation
       pipeline), that pre-serialised structure is returned directly.  This
       avoids a separate ``wiki_pages`` table query and includes derived fields
       such as ``parent_slug`` computed at index time.

    2. **DB fallback** — if ``wiki_structure`` is absent or empty (e.g. the
       repo is being indexed for the first time and no snapshot exists yet),
       the ``wiki_pages`` table is queried and the result is serialised on the
       fly, ordered by ``page_order``.

    Args:
        repo_id (str): The 16-character hex repository identifier, injected
            from the URL path.

    Returns:
        dict: A JSON object:

        .. code-block:: json

            {
                "pages": [
                    {
                        "slug": "overview",
                        "title": "Overview",
                        "parent_slug": null,
                        "page_order": 0
                    },
                    {
                        "slug": "api-layer",
                        "title": "API Layer",
                        "parent_slug": "overview",
                        "page_order": 1
                    }
                ]
            }

    Example:
        .. code-block:: http

            GET /api/repos/a1b2c3d4e5f6a7b8/wiki HTTP/1.1

        Response (200 OK):

        .. code-block:: json

            {"pages": [{"slug": "overview", "title": "Overview",
                        "parent_slug": null, "page_order": 0}]}
    """
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        repo = await s.get(Repository, repo_id)
        if repo and repo.wiki_structure:
            # Fast path: the pipeline stores a fully-resolved structure with
            # derived slugs and parent_slug fields so we can skip the DB query.
            structure = json.loads(repo.wiki_structure)
            return {"pages": structure.get("pages", [])}

        # Fallback: build the listing from the normalised wiki_pages table,
        # preserving the authoring order defined by page_order.
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
    """Fetch the full content of a single wiki page.

    Looks up the most recently updated ``WikiPage`` row matching the given
    ``repo_id`` and ``slug`` combination.  The ``ORDER BY updated_at DESC``
    ensures that if a page was regenerated during an incremental refresh, the
    newest version is returned.

    Args:
        repo_id (str): The 16-character hex repository identifier, injected
            from the URL path.
        slug (str): URL-safe page identifier derived from the page title
            (e.g. ``"api-layer"`` for a page titled ``"API Layer"``).

    Returns:
        dict: A JSON object:

        .. code-block:: json

            {
                "slug": "api-layer",
                "title": "API Layer",
                "content": "# API Layer\\n\\nThis module ...",
                "parent_slug": "overview",
                "updated_at": "2024-01-15T12:34:56+00:00"
            }

        ``content`` is the raw Markdown string.  ``parent_slug`` is ``null``
        for top-level pages.  ``updated_at`` is an ISO-8601 UTC timestamp.

    Raises:
        HTTPException: 404 if no page with the given ``slug`` exists for this
            repository.

    Example:
        .. code-block:: http

            GET /api/repos/a1b2c3d4e5f6a7b8/wiki/api-layer HTTP/1.1

        Response (200 OK):

        .. code-block:: json

            {"slug": "api-layer", "title": "API Layer",
             "content": "# API Layer\\n...", "parent_slug": "overview",
             "updated_at": "2024-01-15T12:34:56+00:00"}
    """
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        result = await s.execute(
            select(WikiPage)
            .where(WikiPage.repo_id == repo_id, WikiPage.slug == slug)
            # Return the most recently generated version if multiple rows exist
            # for the same slug (e.g. after a refresh regenerated the page).
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
