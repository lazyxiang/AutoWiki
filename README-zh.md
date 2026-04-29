# AutoWiki

[English](README.md) | [简体中文](README-zh.md)

为 GitHub 仓库提供的自托管、开源、由 AI 驱动的维基生成器。只需指向一个仓库，即可在几分钟内获得一个可浏览的、与源码链接的维基 —— 完全在您自己的机器上运行，并使用您自己的 API 密钥。

## 功能特性

1. 克隆仓库（浅克隆）
2. 使用 Tree-Sitter 解析源文件（支持 Python, JS/TS, Java, Go, Rust, C/C++, C#）
3. 从导入 (imports) 构建文件级依赖图
4. 将代码分块并嵌入到 FAISS 向量索引中
5. 请求 LLM 生成具有文件分配的逻辑页面层次结构（分两阶段：大纲生成，然后是文件分配）
6. 通过 4 阶段流水线（大纲、草案、事实核查和针对性修订）自下而上生成维基页面 —— 使用快速模型进行低成本处理，使用主模型保证生成质量

生成结果通过 REST API 提供服务，并展示在具有侧边栏导航和对话式问答聊天界面的 Next.js Web UI 中。

## 文档

- [架构指南](docs/architecture-guide-zh.md)
- [配置](docs/configuration-zh.md)
- [命令行界面 (CLI)](docs/cli-zh.md)
- [API](docs/api-zh.md)

---

## 本项目维基

本项目自身的维基文档是由 AutoWiki 通过其生成流水线自动生成的。您可以访问 [AutoWiki 项目维基](https://lazyxiang.github.io/wiki-zh.github.io/) 进行参考。

---

## 快速开始

### 本地运行

**要求：** Python 3.12+, Node.js 22+, Redis, 以及 API 密钥（Anthropic, OpenAI, 或 Google）

```bash
# 1. 安装 Python 包
pip install .

# 2. 构建 Web UI
cd web && npm install && npm run build && cd ..

# 3. 设置您的 API 密钥
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...        # 用于嵌入 (text-embedding-3-small)

# 4. 启动 Redis（如果尚未运行）
redis-server --daemonize yes

# 5. 启动所有服务
autowiki serve
```

打开 http://localhost:3000，粘贴 GitHub URL，然后点击 **Generate Wiki**。

要使用不同的 LLM 或嵌入提供商，请在第 5 步之前设置相关变量：

```bash
# Ollama (完全本地运行，无需 API 密钥)
export AUTOWIKI_LLM_PROVIDER=ollama
export AUTOWIKI_LLM_MODEL=llama3.2
export AUTOWIKI_EMBEDDING_PROVIDER=ollama
export AUTOWIKI_EMBEDDING_MODEL=nomic-embed-text
autowiki serve

# 全部使用 OpenAI
export AUTOWIKI_LLM_PROVIDER=openai
export AUTOWIKI_LLM_MODEL=gpt-4o
export OPENAI_API_KEY=sk-...
autowiki serve

# Google Gemini
export AUTOWIKI_LLM_PROVIDER=google
export AUTOWIKI_LLM_MODEL=gemini-1.5-pro
export AUTOWIKI_EMBEDDING_PROVIDER=google
export AUTOWIKI_EMBEDDING_MODEL=models/text-embedding-004
export GOOGLE_API_KEY=AIzaSy...
autowiki serve
```

### Docker Compose

**要求：** Docker, 以及 API 密钥（Anthropic, OpenAI, 或 Google）

```bash
# 1. 构建镜像（首次运行或源码更改时需要）
docker-compose build

# 2. 启动所有服务（默认使用 Anthropic LLM + OpenAI 嵌入）
ANTHROPIC_API_KEY=sk-ant-... OPENAI_API_KEY=sk-... docker-compose up

# 合并构建与运行
ANTHROPIC_API_KEY=sk-ant-... OPENAI_API_KEY=sk-... docker-compose up --build
```

#### 使用不同提供商运行

```bash
# 全部使用 OpenAI
AUTOWIKI_LLM_PROVIDER=openai AUTOWIKI_LLM_MODEL=gpt-4o \
  OPENAI_API_KEY=sk-... docker-compose up

# 使用 Ollama 完全本地运行（将 OLLAMA_HOST 指向您运行中的实例）
AUTOWIKI_LLM_PROVIDER=ollama AUTOWIKI_LLM_MODEL=llama3.2 \
AUTOWIKI_EMBEDDING_PROVIDER=ollama AUTOWIKI_EMBEDDING_MODEL=nomic-embed-text \
OLLAMA_HOST=http://host.docker.internal:11434 docker-compose up

# 全部使用 Google Gemini
AUTOWIKI_LLM_PROVIDER=google AUTOWIKI_LLM_MODEL=gemini-1.5-pro \
AUTOWIKI_EMBEDDING_PROVIDER=google AUTOWIKI_EMBEDDING_MODEL=models/text-embedding-004 \
GOOGLE_API_KEY=AIzaSy... docker-compose up
```

持久化数据（SQLite, FAISS 索引, 克隆仓库, 维基 Markdown）存储在 `autowiki_data` Docker 卷中。

---

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest tests/ --ignore=tests/e2e

# 运行并查看覆盖率
pytest tests/ --ignore=tests/e2e \
  --cov=worker --cov=api --cov=shared --cov-report=term-missing
```

---

## 项目结构

```text
AutoWiki/
├── api/                    # FastAPI 网关
│   ├── routers/            # REST 接口 (仓库、任务、维基)
│   └── ws/                 # WebSocket 任务进度
├── worker/                 # ARQ 后台 worker
│   ├── jobs.py             # ARQ 任务入口兼容 facade
│   ├── index/              # 完整索引与增量刷新任务编排
│   ├── fast_report/        # Fast Report 服务、检索与任务编排
│   ├── research/           # Deep Research 服务与任务编排
│   ├── pipeline/           # 6 阶段生成流水线
│   ├── llm/                # LLM 提供商适配器
│   └── embedding/          # 嵌入提供商适配器
├── shared/                 # 配置、SQLAlchemy 模型、数据库
├── cli/                    # Typer CLI (索引、列表、启动、配置)
├── web/                    # Next.js 16 前端
└── tests/                  # pytest 测试套件 (591 个后端测试, 80% 覆盖率)
```

---

## 支持的提供商

| 提供商 | LLM | 嵌入 (Embeddings) |
|---|---|---|
| Anthropic | ✅ | — |
| OpenAI / 兼容接口 | ✅ | ✅ |
| Ollama | ✅ | ✅ |
| Google (Gemini) | ✅ | ✅ |

---

## 工作原理

### 架构

```text
浏览器 / CLI
      │
      ▼
┌─────────────────┐      ┌───────┐
│     API 网关     │◄────►│ Redis │
│   (FastAPI)     │      └───────┘
└────────┬────────┘
         │ 入队任务 (enqueue job)
         ▼
┌─────────────────┐
│     Worker      │  (ARQ 后台进程)
│  (6 阶段流水线)  │
└────────┬────────┘
         │ 写入结果
         ▼
┌─────────────────────────────────┐
│  ~/.autowiki/                   │
│    autowiki.db  (SQLite)        │
│    repos/<id>/faiss.index       │
│    repos/<id>/wiki/*.md         │
└─────────────────────────────────┘
```

API 网关是无状态的 —— 它接受请求、读取 SQLite 并将任务推送到 Redis 队列。Worker 运行流水线并将结果写回 SQLite 和磁盘。Next.js 前端仅与 API 通信；它从不直接访问 Worker 或存储。`worker/jobs.py` 保留为 ARQ 任务门面，完整索引编排位于 `worker/index/full.py`，增量刷新编排位于 `worker/index/refresh.py`，Fast Report 编排位于 `worker/fast_report/jobs.py`，Deep Research 编排位于 `worker/research/jobs.py`。

### 流水线 (6 阶段)

每个索引任务按顺序运行六个阶段：

**第 1 阶段 —— 仓库摄入** (`worker/pipeline/ingestion.py`)
使用 GitPython 浅克隆仓库并记录 HEAD 提交 SHA。文件按扩展名和大小（最大 1 MB）进行过滤；排除二进制文件、第三方依赖 (`node_modules`, `.git`, `vendor` 等) 以及生成的代码。

**第 2 阶段 —— AST 分析** (`worker/pipeline/ast_analysis.py`)
每个源文件都通过 Tree-Sitter 进行单次解析，以提取命名实体 —— 类、函数、结构体、接口。结果存储在 `FileAnalysis` 结构中（每个文件的实体列表及其计数和摘要），这为所有下游阶段提供数据。

**第 3 阶段 —— 依赖图** (`worker/pipeline/dependency_graph.py`)
使用语言特定的正则表达式模式从每个文件中提取导入语句，并解析为已知的仓库文件。结果是一个具有连通分量簇的文件级依赖图，供维基规划器理解代码关系。

**第 4 阶段 —— RAG 索引** (`worker/pipeline/rag_indexer.py`)
使用 LangChain 的 `RecursiveCharacterTextSplitter` 将源文件拆分为重叠的分块，由配置的嵌入提供商批量嵌入，并存储在 FAISS `IndexFlatIP`（内积 / 余弦相似度）中。实体感知分块尽可能将整个函数/类保持在一起。传递 `--reuse-index` 可跳过此阶段并重用现有索引。

**第 5 阶段 —— 维基规划** (`worker/pipeline/wiki_planner.py`)
一个两阶段 LLM 过程：第 1 阶段根据架构锚点（目录树、包文档字符串、README 标题）生成页面层次结构（标题、目的、父子关系）；第 2 阶段为每个页面选择 5–8 个代表性源文件（最多 10 个），而不是将每个文件分配给特定页面。每个阶段都会验证其输出并根据反馈进行自重试；在最终失败时，`_heuristic_select_files` 会保留有效页面并根据评分填充其余部分。输出保存为 `wiki.json`（面向用户）和 `wiki_plan.json`（内部使用，包含文件映射和用于增量刷新覆盖的 `all_repo_files`）。使用 `autowiki validate-plan <repo>` 离线检查计划。

**第 6 阶段 —— 页面生成** (`worker/pipeline/page_generator.py`)
页面自下而上生成（先生成叶子页面，后生成父页面），每个页面经过 4 阶段流水线：
- **第 1 步 —— 大纲** (快速模型)：生成结构化大纲，包含计划的章节、图表和要验证的关键主张。
- **第 2 步 —— 草案** (主模型)：使用多查询 RAG 上下文从大纲生成完整的 Markdown。
- **第 3 步 —— 事实核查** (快速模型)：对照源代码验证关键主张和图表。
- **第 4 步 —— 修订** (主模型，有条件触发)：当事实核查失败时应用针对性修复；确定性回退机制会剥离任何仍标记为有问题的部分。

父页面接收其子页面的 Markdown 渲染结果并合成概览，而不是重复内容。提示词缓存可降低重复系统提示词的成本，配置的快速模型 (`AUTOWIKI_LLM_FAST_MODEL`) 处理低成本步骤。

### 数据流（单次索引请求）

```text
POST /api/repos {"url": "github.com/owner/repo"}
  → 验证 URL, 创建 Repository + Job 行 (status=queued)
  → 在 Redis 上入队 run_full_index 任务
  → 返回 {repo_id, job_id}           [202 Accepted]

Worker 领取任务 (`worker.index.full.run_full_index`):
  第 1 阶段  clone/fetch → files[]            进度 5→20
  第 2 阶段  AST parse  → FileAnalysis        进度   →35
  第 3 阶段  dep graph  → DependencyGraph     进度   →45  (内部使用; 无 API)
  第 4 阶段  embed+index → FAISSStore         进度   →55  (使用 --reuse-index 时跳过)
  第 5 阶段  两阶段 LLM 规划 → WikiPlan       进度   →70
  第 6 阶段  自下而上批量 LLM → WikiPages      进度   →100

  任务状态  → "done"
  仓库状态 → "ready"

GET /api/repos/{repo_id}/wiki        → {slug, title, page_order} 列表
GET /api/repos/{repo_id}/wiki/{slug} → {title, content (Markdown)}

WS /ws/jobs/{job_id}                 → 每秒流式传输 {progress, status}
```

---

## 路线图

- **Phase 1** ✅ — 核心流水线（索引 + 静态维基 + REST API + Web UI + CLI）
- **Phase 2** ✅ — 增量刷新、问答聊天、依赖图
- **Phase 2.5** ✅ — 维基质量提升：两阶段规划器、4 阶段页面生成、提示词缓存、快速模型支持、RAG 微调、图表后处理
- **Phase 3** ✅ — 深度研究模式：带有 LLM 规划器的多步 RAG 调查、单步 AST 上下文、综合 Markdown 报告；REST + WebSocket API；`autowiki research` CLI 命令 (PR #20)
- **Phase 4** ✅ — 通过 `.autowiki/wiki.json` 进行用户引导：覆盖页面层次结构、将模块分配给页面、在生成中注入仓库/页面注释 (PR #20)
- **Phase 4.5** ✅ — 规划器健壮性增强 (PR #22)：第 1 阶段大纲提示词中的架构锚点 (Layer C1)、`autowiki validate-plan` 离线诊断 CLI、`_select_files` 中的反馈重试循环、各种错误修复（Gemini JSON、Mermaid、Docker）
- **Phase 4.6** ✅ — 以页面为中心的文件选择 (PR #23)：第 2 阶段从以文件为中心的分配改为以页面为中心的选择（每页 5–8 个文件）；基于评分的预过滤 + 回退 (`_score_file_for_page`, `_heuristic_select_files`)；`WikiPlan.all_repo_files` 用于正确的刷新覆盖；移除孤立文件强制分配
- **Phase 5** ✅ — GitLab/Bitbucket 支持（公开和私有仓库、完整 API 元数据）+ 首页项目搜索 (PR #30)
- **Phase 6** — 混合搜索（关键词 + 语义 BM25/FAISS 融合）

---

## 开源协议

本项目采用 MIT 开源协议。
