from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from shared.config import get_config
from shared.database import init_db
from api.routers import repos, jobs as jobs_router
from api.routers import wiki as wiki_router
from api.routers import chat as chat_router
from api.ws import jobs as ws_jobs


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    cfg.database_path.parent.mkdir(parents=True, exist_ok=True)
    await init_db(str(cfg.database_path))
    yield


app = FastAPI(title="AutoWiki API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(repos.router)
app.include_router(jobs_router.router)
app.include_router(wiki_router.router)
app.include_router(chat_router.router)
app.include_router(ws_jobs.router)
