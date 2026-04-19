# 深度研究与维基引导实施计划

> **针对代办智能体:** 必需子技能: 使用 superpowers:subagent-driven-development (推荐) 或 superpowers:executing-plans 来逐任务执行此计划。步骤使用复选框 (`- [ ]`) 语法进行跟踪。

**目标:** 共同交付 AutoWiki 第 3 阶段（深度研究模式）和第 4 阶段（通过 `.autowiki/wiki.json` 进行用户引导的维基生成） — 不包含 MCP、GitHub Webhooks 或推送触发的自动刷新。

**架构:** 深度研究添加了一个新的长时间运行的 ARQ 任务 (`run_deep_research`)，该任务针对现有的 FAISS + LLM 栈运行三阶段编排器（计划器 → 调查器循环 → 合成器），将结果持久化到新的 `research_reports` 表中，并通过 WebSocket 流式传输渐进式事件。用户引导扩展了现有的维基计划器，使其在第 1 阶段从克隆中加载 `.autowiki/wiki.json`，将 `repo_notes` 注入计划器/页面草稿提示词中，可选地用用户提供的页面列表替换第 1 阶段（大纲），并在第 2 阶段 LLM 调用之前预分配匹配用户声明的 `modules` 前缀的文件。

**技术栈:** FastAPI, ARQ, SQLAlchemy async, pytest asyncio, Next.js 16 App Router, TypeScript, Tailwind v4, shadcn/ui。

**范围排除** (根据 2026-04-14 范围修订):
- **没有** MCP 服务器。REST + WebSocket 是唯一的自动化接口。
- **没有** GitHub webhook 端点 (`POST /webhook/github`)。
- **没有** 推送事件自动刷新。`autowiki refresh` 和 `POST /api/repos/{repo_id}/refresh` 仍然是唯一的刷新触发方式。

---

## 文件结构

### 新文件

| 路径 | 职责 |
|---|---|
| `worker/deep_research.py` | 纯异步编排器: `plan_research`, `investigate_step`, `synthesize_report`。不感知数据库 / WebSocket — 保持单元测试的可行性。 |
| `worker/pipeline/user_steering.py` | `.autowiki/wiki.json` 加载器 + 验证器 + `UserSteering` / `UserPageSpec` 数据类 + 模块前缀匹配器。 |
| `api/routers/research.py` | 深度研究的 REST + WebSocket 端点。负责任务和 WebSocket 之间的事件流粘合。 |
| `cli/commands/research_cmd.py` | Typer 命令，用于 POST 到 `/research`，开启 WebSocket 并打印进度。 |
| `web/components/ResearchPanel.tsx` | 客户端组件: 输入框、计划显示、步骤发现、最终报告 Markdown。 |
| `web/app/[owner]/[repo]/research/page.tsx` | 服务端组件路由 → 渲染 `ResearchPanel`。 |
| `tests/worker/test_deep_research.py` | 针对计划器/调查器/合成器/编排器的单元测试。 |
| `tests/worker/test_user_steering.py` | 针对 `.autowiki/wiki.json` 加载器 + 模块匹配器 + 计划器集成的单元测试。 |
| `tests/api/test_research.py` | 针对 REST + WebSocket 端点的 API 测试。 |
| `tests/cli/test_research_cli.py` | CLI 命令测试。 |

(其余文件修改说明、任务分解等略，见英文版)

---

## 第 3 阶段 — 深度研究

### 任务 1: 添加 `ResearchReport` SQLite 模型
(步骤与代码见英文版)

### 任务 2: 研究计划器 (LLM → 调查计划)
(步骤与代码见英文版)

### 任务 3: 调查器 (RAG 检索 + 每步 LLM 回答)
(步骤与代码见英文版)

### 任务 4: 合成器 (从计划 + 发现生成最终报告)
(步骤与代码见英文版)

### 任务 5: 编排器 `run_deep_research_flow` 带有事件回调
(步骤与代码见英文版)

### 任务 6: ARQ 任务 `run_deep_research`
(步骤与代码见英文版)

### 任务 7: 入队助手 + API 请求架构
(步骤与代码见英文版)

### 任务 8: `GET /research/{job_id}` 返回持久化状态
(步骤与代码见英文版)

### 任务 9: WebSocket 流式传输研究事件
(步骤与代码见英文版)

### 任务 10: CLI `autowiki research`
(步骤与代码见英文版)

### 任务 11: Web API 客户端 + 流式传输 Hook
(步骤与代码见英文版)

### 任务 12: `ResearchPanel` 组件 + 路由
(步骤与代码见英文版)

---

## 第 4 阶段 — 维基引导 (`.autowiki/wiki.json`)

### 任务 14: `UserSteering` 数据类 + 加载器
(步骤与代码见英文版)

### 任务 15: 将 `user_steering` 接入 `run_full_index`
(步骤与代码见英文版)

### 任务 16: 计划器接受 `user_steering` 并注入 repo_notes
(步骤与代码见英文版)

### 任务 17: 用户提供的页面覆盖第 1 阶段（大纲）
(步骤与代码见英文版)

### 任务 18: 将 `page_notes` + `repo_notes` 注入页面草稿提示词
(步骤与代码见英文版)

### 任务 19: 通过 `run_refresh_index` 传播 user_steering
(步骤与代码见英文版)

### 任务 20: 在 API `/wiki` 结构中呈现 "steered" 标志
(步骤与代码见英文版)

### 任务 21: 前端 — 为引导页面添加徽章，显示 repo_notes
(步骤与代码见英文版)

### 任务 22: 文档更新
(步骤与代码见英文版)
