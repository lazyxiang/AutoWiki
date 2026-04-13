# AutoWiki — Product Requirements Document

**Date:** 2026-03-22
**Status:** Approved
**Project:** AutoWiki

---

## 1. Executive Summary

AutoWiki is a self-hosted, open-source AI-powered wiki generator for software repositories. Given a GitHub repository URL, it produces a browsable, interactive wiki containing architecture overviews, module breakdowns, dependency diagrams, source-linked documentation, and a conversational Q&A interface — all running locally with user-supplied API keys.

AutoWiki is designed to close the gaps left by existing tools (DeepWiki, Zread, deepwiki-open, CodeWiki):

- **Accuracy at scale** — Tree-Sitter AST analysis + hierarchical multi-agent generation handles repos up to 1M LOC without losing architectural context.
- **Update freshness** — Incremental re-indexing triggered by GitHub webhooks or CLI keeps the wiki current without full regeneration.
- **Developer experience** — Single `docker-compose up`, minimal config, full-surface access (Web UI + MCP server + CLI).

---

## 2. Background & Competitive Analysis

### 2.1 Products Studied

| Product | Type | Core Approach | Key Strength | Key Gap |
|---|---|---|---|---|
| **DeepWiki** (Cognition AI) | Hosted SaaS | RAG + semantic hypergraph | Deep Research mode, MCP, 50K pre-indexed repos | GitHub only; no auto-sync without badge; LLM undisclosed |
| **Zread** (Zhipu AI) | Hosted SaaS | GLM-4.5 + static analysis | Community Buzz feature, Chinese-native | GitHub only; no auto-sync; MCP paywalled |
| **deepwiki-open** (AsyncFuncAI) | Self-hosted OSS | Next.js + FastAPI + AdalFlow/FAISS | 7 AI providers, GitHub/GitLab/Bitbucket | Generation blocks on large repos; no incremental update; no AST analysis |
| **CodeWiki** (FSoft-AI4Code) | CLI framework | Tree-Sitter AST + hierarchical agents | Benchmarked accuracy (68.79%), scales to 1.4M LOC | No web UI; no Q&A/chat; no MCP; CLI only |

### 2.2 Key Gaps AutoWiki Addresses

1. **No existing self-hosted tool combines AST analysis with RAG** — deepwiki-open uses RAG only (loses architectural context); CodeWiki uses AST only (no chat). AutoWiki uses both.
2. **All existing tools require full re-generation on updates** — AutoWiki introduces incremental re-indexing via file-level change detection.
3. **No self-hosted tool exposes an MCP server** — AutoWiki includes one out of the box.
4. **Generation blocking** — deepwiki-open's monolithic approach blocks on large repos. AutoWiki's Worker + API split makes generation fully async.

---

## 3. Goals & Non-Goals

### Goals

- Generate accurate, navigable wikis for GitHub repositories up to 1M LOC.
- Support multi-turn conversational Q&A and Deep Research mode against the indexed codebase.
- Keep wikis fresh via incremental re-indexing (webhook or CLI-triggered).
- Ship three access surfaces: Web UI, MCP server, CLI.
- Run as a self-hosted Docker deployment with a single `docker-compose up`.
- Be provider-agnostic: ship with Claude Sonnet 4 as the recommended default; support any OpenAI-compatible endpoint.

### Non-Goals (v1)

- GitLab and Bitbucket support (designed for later addition via platform adapter interface).
- Private repository support (architecture accommodates it; not shipped in v1).
- Hosted cloud tier.
- VS Code extension.
- Support for GitHub Issues and Pull Requests indexing.
- Real-time collaboration on wiki pages.

---

## 4. Architecture

### 4.1 System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        User Surfaces                         │
│  Browser (Next.js)  │  CLI (autowiki CLI)  │  MCP Server    │
└──────────┬──────────┴──────────┬───────────┴──────┬─────────┘
           │                     │                   │
           └─────────────────────▼───────────────────┘
                          ┌──────────────┐
                          │  API Gateway │  FastAPI — REST + WebSocket
                          └──────┬───────┘
                                 │  Redis + ARQ job queue
                    ┌────────────▼────────────┐
                    │      Worker Service      │
                    │  1. Repo Ingestion       │
                    │  2. AST Analysis         │
                    │  3. RAG Indexer          │
                    │  4. Wiki Planner         │
                    │  5. Page Generator       │
                    └─────────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │       Storage Layer      │
                    │  SQLite (jobs/metadata)  │
                    │  FAISS (vectors/repo)    │
                    │  Markdown files (wiki)   │
                    └─────────────────────────┘
```

### 4.2 Service Decomposition

**API Gateway** (`api/`) — FastAPI application. Handles all inbound requests: REST endpoints, WebSocket streaming, MCP server, and GitHub webhook. Enqueues jobs to Redis. Never performs long-running computation itself.

**Worker Service** (`worker/`) — Python process pool managed by ARQ. Executes the generation pipeline. Scales horizontally by adding worker replicas.

**Frontend** (`web/`) — Next.js 16 application. Communicates with the API Gateway only. Stateless.

**Storage** — SQLite for structured metadata; FAISS indexes persisted to disk per repository; Markdown files for wiki content.

### 4.3 Generation Pipeline (Five Main Stages)

| Stage | Responsibility | Key Technology |
|---|---|---|
| **1. Repo Ingestion** | Clone/fetch repo; apply file filters (`.autowikiignore` + built-in rules); detect changes via commit SHA diff | `gitpython` |
| **2. AST Analysis** | Parse source files with Tree-Sitter; extract functions, classes, imports, call graphs; build unified dependency graph + module tree | `tree-sitter` (9 languages) |
| **3. RAG Indexer** | Chunk documents with overlap; generate embeddings; build/update FAISS index; persist per `{repo_hash}` | `langchain` splitter, configurable embedding provider, `faiss-cpu` |
| **4. Wiki Planner** | Feed AST dependency graph + repo structure to LLM; produce hierarchical JSON page plan; validate + retry on malformed output (up to 3 attempts) | LLM structured output |
| **5. Page Generator** | Per page: RAG retrieval + AST graph slice injected as context; LLM generates page; recurse for large modules via sub-agents; stream results | Hierarchical agent loop |

*Note: Architecture diagrams are synthesized per-page by the LLM during Stage 5.*

### 4.4 Incremental Re-Indexing

Every indexed repo stores the HEAD commit SHA at index time. On refresh trigger (webhook `push` event or `autowiki refresh`):

1. Fetch current HEAD SHA from GitHub API.
2. Diff against stored SHA to identify changed files.
3. Determine affected **pages**: use `get_affected_pages` to map changed files to existing wiki pages.
4. Re-run stages 1–5 only for affected pages.
5. Update FAISS index for changed chunks only (delete-and-insert by chunk ID).
6. Update stored commit SHA.

This is the primary freshness differentiator over all competing products.

### 4.5 Supported Languages (AST Analysis)

Python, JavaScript, TypeScript, Java, Go, Rust, C, C++, C# — 9 languages via Tree-Sitter grammars. Files in unsupported languages are still indexed via RAG (text-only, no AST graph).

---

## 5. Data Models

### 5.1 SQLite Schema

```sql
repositories (
  id            TEXT PRIMARY KEY,   -- sha256(platform:owner/repo)
  owner         TEXT NOT NULL,
  name          TEXT NOT NULL,
  platform      TEXT DEFAULT 'github',
  last_commit   TEXT,               -- HEAD SHA at last index
  status        TEXT,               -- pending | indexing | ready | error
  indexed_at    DATETIME,
  wiki_language TEXT                -- en | zh
)

jobs (
  id            TEXT PRIMARY KEY,
  repo_id       TEXT,
  type          TEXT,               -- full_index | refresh
  status        TEXT,               -- queued | running | done | failed
  progress      INTEGER,            -- 0-100
  status_description TEXT,          -- detailed step status
  error         TEXT,
  created_at    DATETIME,
  finished_at   DATETIME
)

wiki_pages (
  id            TEXT PRIMARY KEY,
  repo_id       TEXT,
  slug          TEXT,               -- derived from title
  title         TEXT,
  content       TEXT,               -- markdown
  parent_slug   TEXT,
  page_order    INTEGER,
  description   TEXT                -- page purpose
)
```

---

## Status: Phase 2 Complete (2026-04-12)

Phase 1 and Phase 2 are fully implemented. The system now supports hierarchical wiki generation, incremental refresh, Q&A chat, and per-page architecture diagrams.
