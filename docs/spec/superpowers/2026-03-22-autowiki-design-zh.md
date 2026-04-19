# AutoWiki — 产品需求文档 (PRD)

**日期:** 2026-03-22
**状态:** 已批准 — 第 1, 2, 2.5, 3 和 4 阶段已实现。第 5 阶段待定。
**项目:** AutoWiki

> **[部分内容已过时]** 这是原始 PRD。已实现系统中的主要偏差如下：
>
> - **存储布局** (§5.2): `ast/module_tree.json` 已被 `ast/wiki_plan.json` (内部规划) 和 `wiki/wiki.json` (面向用户的结构) 取代。`ast/architecture.mmd` 未被生成 —— 第 7 阶段 (图表合成) 在发布前已被移除。
> - **LLM 提供商配置** (§5.3): `gemini-1.5-pro` 现已弃用；请使用 `google-genai` SDK 配合 `gemini-2.5-pro` 或类似模型。`google-generativeai` SDK 也已弃用。
> - **第 3 阶段范围变更 (2026-04-14):** MCP 服务端工具 (`read_wiki_structure`, `read_wiki_page`, `search_wiki`, `ask_question`) 推迟至第 5 阶段。已实现“深度研究”模式：结合 LLM 规划器的多步 RAG 调查、逐步 AST 上下文检索以及综合 Markdown 报告。通过 `POST /api/repos/{repo_id}/research`、`WS /ws/repos/{repo_id}/research/{job_id}` 和 `autowiki research` CLI 命令公开。
> - **第 4 阶段范围变更 (2026-04-14):** GitHub Webhooks 和推送触发的自动刷新推迟至第 5 阶段。已实现“用户引导的 Wiki 结构”（通过 `.autowiki/wiki.json`）：允许覆盖页面层级、将源码模块分配给特定页面，并在 Wiki 生成中注入仓库/页面级别的注释。

---

## 1. 执行摘要

AutoWiki 是一个自托管、开源、基于 AI 的软件仓库 Wiki 生成器。给定一个 GitHub 仓库 URL，它能生成一个可浏览的交互式 Wiki，包含架构概览、模块细分、依赖图、源码链接文档以及一个对话式问答接口 —— 所有这些都通过用户提供的 API 密钥在本地运行。

AutoWiki 旨在弥补现有工具 (DeepWiki, Zread, deepwiki-open, CodeWiki) 的不足：

- **大规模下的准确性** — Tree-Sitter AST 分析 + 分层多智能体生成技术，可处理高达 100 万行代码 (1M LOC) 的仓库，且不丢失架构上下文。
- **更新及时性** — 通过 GitHub Webhooks 或 CLI 触发的增量重新索引，无需完全重新生成即可保持 Wiki 内容最新。
- **开发者体验** — 单个 `docker-compose up` 命令即可运行，极简配置，全方位访问接口 (Web UI + MCP 服务端 + CLI)。

---

## 2. 背景与竞争分析

### 2.1 竞品研究

| 产品 | 类型 | 核心方法 | 主要优势 | 主要差距 |
|---|---|---|---|---|
| **DeepWiki** (Cognition AI) | 托管 SaaS | RAG + 语义超图 | 深度研究模式, MCP, 5 万个预索引仓库 | 仅限 GitHub；无徽章则不自动同步；LLM 未公开 |
| **Zread** (智谱 AI) | 托管 SaaS | GLM-4.5 + 静态分析 | Community Buzz (社区热度) 功能, 原生中文支持 | 仅限 GitHub；无自动同步；MCP 需付费 |
| **deepwiki-open** (AsyncFuncAI) | 自托管开源 | Next.js + FastAPI + AdalFlow/FAISS | 支持 7 个 AI 提供商, GitHub/GitLab/Bitbucket | 大规模仓库生成会阻塞；无增量更新；无 AST 分析 |
| **CodeWiki** (FSoft-AI4Code) | CLI 框架 | Tree-Sitter AST + 分层智能体 | 经基准测试的准确性 (68.79%), 可扩展至 140 万行代码 | 无 Web UI；无问答/聊天；无 MCP；仅 CLI |

### 2.2 AutoWiki 解决的关键空白

1. **现有自托管工具均未结合 AST 分析与 RAG** — deepwiki-open 仅使用 RAG (丢失架构上下文)；CodeWiki 仅使用 AST (无聊天功能)。AutoWiki 两者兼备。
2. **现有工具在更新时均需完全重新生成** — AutoWiki 引入了基于文件级变更检测的增量重新索引。
3. **现有自托管工具均未提供 MCP 服务端** — AutoWiki 开箱即用。
4. **生成阻塞问题** — deepwiki-open 的单体方法在处理大型仓库时会阻塞。AutoWiki 的 Worker + API 分离使生成过程完全异步。

---

## 3. 目标与非目标

### 目标

- 为高达 100 万行代码 (1M LOC) 的 GitHub 仓库生成准确、可导航的 Wiki。
- 支持针对索引代码库的多轮对话问答和“深度研究”模式。
- 通过增量重新索引 (Webhook 或 CLI 触发) 保持 Wiki 内容最新。
- 提供三种访问接口：Web UI, MCP 服务端, CLI。
- 作为单命令 `docker-compose up` 的自托管 Docker 部署运行。
- 提供商无关性：以 Claude Sonnet 4 作为推荐默认模型；支持任何兼容 OpenAI 的端点。

### 非目标 (v1)

- GitLab 和 Bitbucket 支持 (设计为稍后通过平台适配器接口添加)。
- 私有仓库支持 (架构上已支持，但在 v1 中不提供)。
- 托管云服务。
- VS Code 扩展。
- 支持 GitHub Issues 和 Pull Requests 的索引。
- Wiki 页面的实时协作。

---

## 4. 架构

### 4.1 系统概览

```
┌──────────────────────────────────────────────────────────────────┐
│                           用户界面                                │
│  浏览器 (Next.js)  │  CLI (autowiki CLI)  │  MCP 服务端 (第 5 阶段) │
└──────────┬──────────┴──────────┬───────────┴──────┬──────────────┘
           │                     │                   │
           └─────────────────────▼───────────────────┘
                          ┌──────────────┐
                          │    API 网关   │  FastAPI — REST + WebSocket
                          └──────┬───────┘
                                 │  Redis + ARQ 任务队列
                    ┌────────────▼────────────┐
                    │        Worker 服务       │
                    │  1. 仓库摄取 (Ingestion)  │
                    │  2. AST 分析             │
                    │  3. 依赖图 (Dep Graph)    │
                    │  4. RAG 索引器           │
                    │  5. Wiki 规划器          │
                    │  6. 页面生成器           │
                    └─────────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │         存储层           │
                    │  SQLite (任务/元数据)    │
                    │  FAISS (向量/仓库)      │
                    │  Markdown 文件 (Wiki)   │
                    └─────────────────────────┘
```

### 4.2 服务分解

**API 网关** (`api/`) — FastAPI 应用。处理所有入站请求：REST 端点和 WebSocket 流。将任务排入 Redis 队列。其自身从不执行长时间计算。(MCP 服务端和 GitHub Webhook 推迟至第 5 阶段。)

**Worker 服务** (`worker/`) — 由 ARQ 管理的 Python 进程池。执行六阶段生成流水线。通过增加 Worker 副本实现水平扩展。

**前端** (`web/`) — Next.js 16.2.1 应用。仅与 API 网关通信。无状态。

**存储** — 使用 SQLite 存储结构化元数据；每个仓库的 FAISS 索引持久化到磁盘；Markdown 文件存储 Wiki 内容。

### 4.3 生成流水线 (六个阶段)

| 阶段 | 职责 | 关键技术 |
|---|---|---|
| **1. 仓库摄取** | 克隆/拉取仓库；应用文件过滤器 (`.autowikiignore` + 内置规则)；通过 commit SHA 差异检测变更 | `gitpython` |
| **2. AST 分析** | 使用 Tree-Sitter 解析源文件；提取函数、类、导入；每个文件执行单次 `FileAnalysis` | `tree-sitter` (支持 9 种语言) |
| **3. 依赖图** | 构建文件级导入图；通过 BFS 拆分为集群，以提供跨文件关系上下文 | 内存中的 `DependencyGraph`；不持久化到磁盘 |
| **4. RAG 索引器** | 对文档进行带重叠的分块；生成嵌入 (Embedding)；构建/更新 FAISS 索引；按 `{repo_hash}` 持久化；可通过 `reuse_index=True` 跳过 | `langchain` 分块器, 可配置嵌入提供商, `faiss-cpu` |
| **5. Wiki 规划器** | 两阶段 LLM 规划：阶段 1 大纲 (层级/标题/用途)，阶段 2 文件分配；每个阶段都会验证并自动重试 → `WikiPlan` | LLM 结构化输出 |
| **6. 页面生成器** | 每页由自底向上的 4 次传递协调器处理：Pass 1 大纲 (快速模型), Pass 2 草稿, Pass 3 事实核查 (快速模型), Pass 4 针对性修改 (可选)；Mermaid 后处理 | `generate_batch` 并发, `fast_llm` |

### 4.4 增量重新索引

每个索引过的仓库在索引时都会存储 HEAD commit SHA。在触发刷新 (Webhook `push` 事件或 `autowiki refresh`) 时：

1. 从 GitHub API 获取当前 HEAD SHA。
2. 与存储的 SHA 进行对比以识别变更文件。
3. 确定受影响的 **模块 (modules)**：模块是顶层包目录 (例如 `src/auth/`, `src/api/`)。变更文件属于其目录路径与文件路径最长前缀匹配的那个模块。仓库根目录的文件属于一个合成的 `root` 模块。
4. 仅针对受影响的模块重新运行第 1–6 阶段。
5. 仅针对变更的分块更新 FAISS 索引 (通过分块 ID 执行“删除并插入”)。
6. 更新存储的 commit SHA。

这是 AutoWiki 相对于所有竞争产品的主要新鲜度优势。

### 4.5 支持的语言 (AST 分析)

Python, JavaScript, TypeScript, Java, Go, Rust, C, C++, C# — 通过 Tree-Sitter 语法支持 9 种语言。不支持语言的文件仍会通过 RAG 进行索引 (仅文本，无 AST 图)。

---

## 5. 数据模型

### 5.1 SQLite 模式

```sql
repositories (
  id            TEXT PRIMARY KEY,   -- sha256(platform:owner/repo)
  owner         TEXT NOT NULL,
  name          TEXT NOT NULL,
  platform      TEXT DEFAULT 'github',
  last_commit   TEXT,               -- 上次索引时的 HEAD SHA
  status        TEXT,               -- pending | indexing | ready | error
  indexed_at    DATETIME,
  wiki_path     TEXT                -- Wiki Markdown 目录的绝对路径
)

jobs (
  id            TEXT PRIMARY KEY,   -- UUID
  repo_id       TEXT REFERENCES repositories(id),
  type          TEXT,               -- full_index | refresh | page_regen
  status        TEXT,               -- queued | running | done | failed
  progress      INTEGER DEFAULT 0,  -- 0–100
  error         TEXT,
  created_at    DATETIME,
  finished_at   DATETIME
)

wiki_pages (
  id            TEXT PRIMARY KEY,
  repo_id       TEXT REFERENCES repositories(id),
  slug          TEXT,
  title         TEXT,
  content       TEXT,               -- Markdown 内容
  page_order    INTEGER,
  parent_slug   TEXT,               -- 顶层页面为 null
  updated_at    DATETIME
)

chat_sessions (
  id            TEXT PRIMARY KEY,
  repo_id       TEXT REFERENCES repositories(id),
  created_at    DATETIME
)

chat_messages (
  id            TEXT PRIMARY KEY,
  session_id    TEXT REFERENCES chat_sessions(id),
  role          TEXT,               -- user | assistant
  content       TEXT,
  created_at    DATETIME
)
```

### 5.2 文件系统布局

```
~/.autowiki/
  repos/
    {repo_hash}/
      clone/              ← 浅层 git 克隆
      faiss.index         ← FAISS 向量索引
      faiss.meta.pkl      ← 分块元数据 (文件, 行范围, slug)
      wiki/
        index.md
        {slug}.md         ← 每个 Wiki 页面一个文件
      ast/
        wiki_plan.json    ← 带有文件映射的内部 Wiki 规划 (取代 module_tree.json)
        # 依赖图在内存中计算为 DependencyGraph；不持久化到磁盘
  autowiki.db             ← SQLite 数据库
  logs/
    worker.log
    api.log
```

### 5.3 LLM 提供商配置

默认提供商: **Anthropic Claude Sonnet 4** (`claude-sonnet-4-6`)。可通过 `autowiki.yml` 或环境变量覆盖：

```yaml
# autowiki.yml
llm:
  provider: google             # anthropic, google, openai, openai-compatible, ollama
  model: gemini-1.5-pro       # 或 gemini-1.5-flash, claude-sonnet-4-6, gpt-4o 等
  api_key: ${GOOGLE_API_KEY}

embedding:
  provider: google             # openai, google, ollama
  model: text-embedding-004
  api_key: ${GOOGLE_API_KEY}
```

**第 1 阶段提供商支持:** `anthropic`, `google` (Gemini), `openai`, `openai-compatible` (任何兼容 OpenAI 的基准 URL), `ollama` (本地)。在第 1 阶段，这些提供商均支持文本生成和嵌入。

任何兼容 OpenAI 的端点均可通过 `provider: openai-compatible` + `base_url` 使用。切换嵌入提供商需要重新索引 (向量空间不兼容 —— 这会在 UI/CLI 中明确提示)。

**配置发现与优先级 (从高到低):**
1. 环境变量 (例如 `AUTOWIKI_LLM_PROVIDER`, `ANTHROPIC_API_KEY`)
2. 当前工作目录下的 `autowiki.yml`
3. `~/.autowiki/autowiki.yml` (用户全局配置)
4. 内置默认值

API 服务和 Worker 服务在启动时都从同一源读取配置。在 Docker Compose 中，每个容器 `environment:` 块中的环境变量优先级高于挂载的 `autowiki.yml`。

---

## 6. API 设计

### 6.1 REST 端点

```
POST   /api/repos                              提交仓库进行索引
GET    /api/repos/{repo_id}                    仓库状态 + 元数据
GET    /api/repos/{repo_id}/wiki               列出所有 Wiki 页面
GET    /api/repos/{repo_id}/wiki/{slug}        获取单个 Wiki 页面 (Markdown)
POST   /api/repos/{repo_id}/refresh            触发增量重新索引 *(第 2 阶段)*
GET    /api/jobs/{job_id}                      轮询任务状态 + 进度 (0–100)

POST   /api/repos/{repo_id}/chat               创建聊天会话
GET    /api/repos/{repo_id}/chat/{session_id}  获取聊天历史
WS     /ws/repos/{repo_id}/chat/{session_id}   流式聊天 (WebSocket)
POST   /api/repos/{repo_id}/research           启动“深度研究”任务
WS     /ws/repos/{repo_id}/research/{job_id}   流式传输研究进度
WS     /ws/jobs/{job_id}                       流式传输任务进度 0–100 (用于 JobProgressBar)

POST   /webhook/github                         GitHub 推送 Webhook *(第 5 阶段 — 推迟)*
```

**关键端点模式:**

`POST /api/repos` — 请求:
```json
{ "url": "https://github.com/owner/repo" }
```
响应 `202 Accepted`:
```json
{ "repo_id": "abc123", "job_id": "uuid-...", "status": "queued" }
```

`POST /api/repos/{repo_id}/chat` — 创建新会话 (无需请求体)。
响应 `201 Created`:
```json
{ "session_id": "uuid-..." }
```
调用者随后在 `/ws/repos/{repo_id}/chat/{session_id}` 打开 WebSocket，并发送/接收 JSON 消息: `{ "role": "user"|"assistant", "content": "..." }`。

`POST /webhook/github` — 需要 `X-Hub-Signature-256` HMAC 头部 (原始负载的 SHA-256，使用 `autowiki.yml` 中 `webhook.github_secret` 配置的秘钥签名)。签名验证失败的请求返回 `401`。这防止了 API 暴露在公共地址时未经授权的刷新触发。

### 6.2 CLI (`autowiki`)

```bash
# 第 1 阶段
autowiki index github.com/owner/repo           # 索引一个仓库
autowiki index github.com/owner/repo --force   # 强制完整重新索引
autowiki list                                  # 列出已索引仓库及状态
autowiki serve [--port 3000]                   # 启动全栈服务 (API + worker + web UI)
autowiki config show                           # 显示当前配置
autowiki config set llm.provider anthropic     # 更新配置值

# 第 2 阶段
autowiki refresh github.com/owner/repo         # 增量刷新 (基于 commit-SHA 差异)
autowiki chat github.com/owner/repo "..."      # 终端问答 (CLI 不支持多轮对话)

# 第 3 阶段
autowiki research github.com/owner/repo "..."  # 触发深度研究；将报告打印到标准输出
```

`autowiki serve` 在单个前台进程中启动全栈服务：它以子进程形式启动 FastAPI API 服务器、ARQ worker 和 Next.js 前端。它是非 Docker 环境下的入口点。在 Docker Compose 中，每个服务独立运行；不使用 `autowiki serve`。

### 6.3 MCP 服务端工具 *(第 5 阶段 — 推迟)*

> **注意:** MCP 服务端原计划在第 3 阶段，但为了优先支持“深度研究”模式而推迟至第 5 阶段。详见上方“过时”横幅中的第 3 阶段范围变更。

| 工具 | 描述 |
|---|---|
| `read_wiki_structure` | 返回仓库的完整页面层级 |
| `read_wiki_page` | 通过 slug 返回页面的 Markdown 内容 |
| `search_wiki` | 跨 Wiki 和代码库进行语义搜索 |
| `ask_question` | 单轮 RAG 问答 |
| `deep_research` | 多步调查；返回结构化研究报告 |

传输协议: `stdio` (本地) 或 `SSE` (远程)。本地使用无需身份验证。通过标准的 `mcp.json` 配置。

---

## 7. 问答与深度研究

### 7.1 多轮对话

- 每轮 RAG 检索: 从 FAISS 索引中获取 Top-K 分块，按余弦相似度排序。
- 对话历史注入 LLM 上下文 (滑动窗口，默认最近 **10 轮**；可通过 `autowiki.yml` 中的 `chat.history_window` 配置)。
- 响应通过 WebSocket 流式传输。
- 包含源码引用: 每个响应都会引用源码文件 + 行范围。
- 会话历史持久化在 SQLite 中 (`chat_sessions` / `chat_messages`)。
- MCP `ask_question` 工具是单轮的 (无状态)；多轮上下文仅在 WebSocket 会话中维护。

### 7.2 深度研究模式

```
用户提问
      │
      ▼
研究规划器 (LLM)
  → 生成: 研究规划 (JSON, 3–5 个调查步骤)
      │
      ▼ (循环, 最多 5 轮)
调查智能体
  → 每步执行 RAG 检索 + AST 图遍历
  → 向客户端流式传输中间发现
      │
      ▼
综合器 (LLM)
  → 最终结论: 摘要 + 源码引用 + 置信度级别
```

“深度研究”可在 Web UI (ResearchPanel) 和 CLI (`autowiki research github.com/owner/repo "..."`) 中使用。MCP `deep_research` 工具推迟至第 5 阶段。

---

## 8. 前端 (Web UI)

### 8.1 技术栈

- Next.js 16.2.1 (App Router) + TypeScript
- Tailwind v4 (仅 CSS，无 `tailwind.config.ts`)
- @base-ui/react (组件原语；非 shadcn/ui 或 @radix-ui/react)
- Mermaid.js (图表渲染)
- D3 或 react-flow (交互式依赖图)

### 8.2 路由

```
/                          首页: URL 输入, 索引新仓库
/repos                     所有已索引仓库及状态
/{owner}/{repo}            Wiki 索引页 + 侧边栏导航
/{owner}/{repo}/{slug}     单个 Wiki 页面
/{owner}/{repo}/chat       多轮对话界面
/{owner}/{repo}/research   深度研究界面
/{owner}/{repo}/graph      交互式依赖图
```

### 8.3 关键组件

| 组件 | 描述 |
|---|---|
| `IndexForm` | GitHub URL 输入；提交时触发索引任务 |
| `JobProgressBar` | 索引过程中的实时进度；由 `WS /ws/jobs/{job_id}` 驱动 (流式传输 0–100 整数进度事件) |
| `WikiSidebar` | 分层页面树，可折叠，支持键盘导航 |
| `WikiPage` | Markdown 渲染器，支持语法高亮 + Mermaid 块 |
| `ChatPanel` | 带源码引用的流式多轮对话界面 |
| `ResearchPanel` | 渐进式展示: 研究规划 → 发现 → 最终结论 |
| `DependencyGraph` | 交互式力导向模块关系图 |

### 8.4 UX 原则

1. **单命令启动** — `docker-compose up`；UI 在 `localhost:3000` 运行；首次索引时提示输入 API 密钥。
2. **渐进式渲染** — Wiki 页面生成后即刻出现 (流式)；用户在 < 60 秒内即可看到小型仓库的内容。
3. **源码引用** — 生成的每个段落都链接到其来源的源码文件 + 行范围。
4. **图表优先** — 架构图出现在每个主要页面的顶部；Mermaid 源码始终可展开/复制。
5. **深色模式默认** — 提供浅色模式切换。

---

## 9. 面向用户的配置

### 9.1 `.autowikiignore` *(第 2 阶段)*

仓库可在根目录包含 `.autowikiignore` (使用 `.gitignore` 语法) 来控制 AutoWiki 索引的内容。在第 1 阶段，仅应用内置排除规则；`.autowikiignore` 支持在第 2 阶段上线。

```
# .autowikiignore
node_modules/
dist/
*.test.ts
*.spec.py
fixtures/
__pycache__/
migrations/
```

AutoWiki 还会对常见的非源码目录 (`node_modules`, `vendor`, `.git`, 构建输出, 二进制文件等) 应用内置排除规则。

### 9.2 `.autowiki/wiki.json` (可引导性) *(第 4 阶段)*

仓库可包含 `.autowiki/wiki.json` 来引导 Wiki 生成 (灵感来自 DeepWiki 的 `.devin/wiki.json`)。该功能在第 4 阶段上线；此前阶段仅使用 LLM 生成的页面规划。

```json
{
  "repo_notes": [
    "本项目使用在 src/core/bus.ts 中定义的自定义事件总线 —— 请将其视为核心通信骨架。",
    "有意将 `legacy/` 目录排除在文档之外。"
  ],
  "pages": [
    { "title": "架构概览", "modules": ["src/core", "src/api"] },
    { "title": "身份验证系统", "modules": ["src/auth"] },
    { "title": "数据流水线", "modules": ["src/pipeline", "src/workers"] }
  ]
}
```

如果定义了 `pages`，它将覆盖 LLM 生成的页面规划。`repo_notes` 会作为额外上下文注入到每个 LLM 调用中。

---

## 10. 错误处理

| 故障 | 行为 |
|---|---|
| LLM API 速率限制 / 超时 | 带抖动的指数退避 (3 次重试)；重试耗尽后任务标记为 `failed` 并显示操作建议 |
| 格式错误的 LLM 输出 (无效 JSON 页面规划) | 结构化输出验证 + 纠错重试提示 (最多 3 次)；回退到扁平页面结构 |
| 仓库超出大小限制 (>50 万个文件) | 克隆前文件计数检查；拒绝并显示明确消息，建议使用 `.autowikiignore` (第 2 阶段起可用) 或通过 `autowiki.yml` 文件过滤器缩小范围 |
| FAISS 索引损坏 | 加载时自动检测；删除并触发重新索引并通知用户 |
| GitHub API 速率限制 (Webhook) | 缓存 Webhook 任务；延迟处理；在 UI 中显示速率限制状态 |
| 嵌入提供商不可用 | 阻塞新索引任务；提供现有的缓存 Wiki；清晰显示错误 |
| Tree-Sitter 解析失败 (不支持语言) | 跳过该文件的 AST 分析；继续执行仅文本的 RAG；记录警告日志 |
| Worker 在任务中途崩溃 | ARQ 最多重试任务 2 次；耗尽后标记为 `failed` 并显示最后已知错误 |

---

## 11. 测试策略

| 层级 | 方法 | 工具 |
|---|---|---|
| **单元测试** | AST 解析器, 文件过滤器, 分块器, Mermaid 验证器 — 纯函数测试 | `pytest` |
| **集成测试** | 针对提交到测试套件的小型 fixture 仓库运行完整 Worker 流水线；断言页面计数、图表存在性、无崩溃 | `pytest` + fixture 仓库 |
| **API 测试** | 所有 REST 端点；使用录制的 fixture 模拟 LLM 响应 | FastAPI `TestClient` |
| **前端测试** | 组件渲染；关键用户流程 (索引 → Wiki 查看 → 聊天) | `vitest` + React Testing Library + Playwright |
| **LLM 回归测试** | 黄金文件 (Golden-file) 测试：固定 fixture 仓库 + 固定模型；对比输出与存储的基准线 | 自定义 pytest 插件 |

**覆盖率目标:**
- 单元 + 集成 + API: `worker/` 和 `api/` Python 包的行覆盖率 ≥ 80%。
- 前端: 无硬性覆盖率目标；Playwright E2E 测试必须覆盖所有关键路径。

**CI 流水线 (GitHub Actions):**
- 每次 Pull Request 运行单元、集成和 API 测试 (无需 LLM API 密钥 —— 所有 LLM 调用均被模拟)。
- 每次 PR 运行前端测试 (vitest + Playwright)。
- LLM 回归测试 (黄金文件) 仅在定时夜间任务中运行，使用仓库密钥获取 API 密钥。失败会自动创建 GitHub Issue 但不阻塞合并。

---

## 12. 非功能性需求

| 需求 | 目标 |
|---|---|
| 首个 Wiki 页面生成时间 (≤ 50K LOC 仓库) | < 3 分钟 |
| 增量刷新延迟 (单个变更文件) | < 60 秒 |
| 聊天首个 Token 延迟 | < 2 秒 |
| 深度研究完成时间 | < 3 分钟 |
| 最大支持仓库大小 | 1M LOC (配合 `.autowikiignore`) |
| 每个已索引仓库的存储占用 | < 500MB |
| 大规模下的总存储 | 约 500MB × N 个仓库；v1 中无自动清理 —— 用户必须通过 `autowiki list` + `autowiki delete` 手动删除仓库。磁盘耗尽会导致新索引任务失败，UI 和任务状态中显示 `DISK_FULL` 错误。 |
| Docker 镜像大小 (总计) | < 2GB |
| 启动时间 (`docker-compose up` 到就绪) | < 30 秒 |
| AST 支持的语言 | 9 种 (Python, JS, TS, Java, Go, Rust, C, C++, C#) |

---

## 13. Docker 部署

```yaml
# docker-compose.yml (示例)
services:
  api:
    build: ./api
    ports: ["3001:3001"]
    environment:
      - REDIS_URL=redis://redis:6379
      - DATABASE_PATH=/data/autowiki.db
    volumes:
      - autowiki_data:/data

  worker:
    build: ./worker
    environment:
      - REDIS_URL=redis://redis:6379
      - DATABASE_PATH=/data/autowiki.db
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    volumes:
      - autowiki_data:/data

  web:
    build: ./web
    ports: ["3000:3000"]
    environment:
      - NEXT_PUBLIC_API_URL=http://api:3001

  redis:
    image: redis:7-alpine

volumes:
  autowiki_data:
```

---

## 14. 分阶段交付

### 第 1 阶段 — 核心 (MVP)
- Worker 流水线: 第 1–5 阶段 (摄取, AST, RAG, 规划, 生成)
- API 网关: 索引, 状态, Wiki CRUD 端点
- Web UI: IndexForm, JobProgressBar, WikiSidebar, WikiPage
- CLI: `index`, `list`, `serve`
- Docker Compose 部署

### 第 2 阶段 — 聊天、图表与刷新
- 第 6 阶段: 图表合成
- 多轮对话 (WebSocket 流式传输)
- UI 中的 ChatPanel
- DependencyGraph 视图
- `autowiki chat` CLI 命令
- `autowiki refresh` CLI 命令 (基于 commit-SHA 差异的增量索引；暂无 Webhook)
- `.autowikiignore` 支持

### 第 3 阶段 ✅ — 深度研究 (已于 2026-04-14 在 PR #20 中实现)
- 深度研究模式: LLM 规划器 → 多步 RAG 调查器 → LLM 综合器
- `ResearchReport` 模型 (`research_reports` 表)
- `run_deep_research` ARQ 任务
- REST: `POST /api/repos/{repo_id}/research`, `GET /api/repos/{repo_id}/research/{job_id}`
- WebSocket: `WS /ws/repos/{repo_id}/research/{job_id}`
- Web UI 中的 ResearchPanel
- `autowiki research` CLI 命令
- *MCP 服务端推迟至第 5 阶段*

### 第 4 阶段 ✅ — 用户引导 (已于 2026-04-14 在 PR #20 中实现)
- `.autowiki/wiki.json` 可引导性：覆盖页面层级，将源码模块分配给页面
- 在 Wiki 生成提示词中注入仓库级和页面级的注释
- Wiki 结构 API 中的 `has_user_notes` 指示器 (UI 中的蓝色徽章)
- `load_user_steering` + `assign_by_modules` 工具函数
- *GitHub Webhooks 和推送触发的自动刷新推迟至第 5 阶段*

### 第 5 阶段 — 完善与扩展
- MCP 服务端工具 (`read_wiki_structure`, `read_wiki_page`, `search_wiki`, `ask_question`, `deep_research`) — 从第 3 阶段推迟
- GitHub Webhook 推送触发的自动刷新 (`POST /webhook/github`) — 从第 4 阶段推迟
- 平台适配器接口 (GitLab, Bitbucket 存根)
- 私有仓库支持 (GitHub PAT)
- 混合搜索 (关键词 + 语义)
- 黄金文件 LLM 回归测试
- 性能分析与优化

---

## 15. 待解决问题

1. **嵌入提供商默认值** — 即使使用 Anthropic 进行生成，OpenAI `text-embedding-3-small` 也需要单独的 OpenAI API 密钥。AutoWiki 是否应默认使用可在本地运行的嵌入模型 (例如通过 Ollama) 以减少对提供商的依赖？
2. **FAISS 与替代方案** — FAISS 是进程内且零基础设施的，但不支持混合“关键词 + 语义”搜索。v1 是否应考虑 `sqlite-vec` (SQLite 向量扩展) 以减少依赖足迹？
3. **页面限制** — AutoWiki 是否应强制执行每个 Wiki 的最大页面计数 (类似于 DeepWiki 的 30/80 限制) 以控制生成成本和时间？还是保持不限并让 `.autowikiignore` 发挥作用？

**已解决:**

4. ~~Web UI 的身份验证~~ — **已解决:** API 和 Web UI 默认绑定到 `127.0.0.1` (仅限本地访问)。需要网络暴露的用户必须在 `autowiki.yml` 中明确设置 `server.host: 0.0.0.0`。当 `host` 设置为非回环地址时，启动日志会发出显眼警告。为在网络上公开服务的用户提供了一个可选的 Bearer Token 认证层 (`server.auth_token`)；本地使用无需此项。
