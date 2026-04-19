# 方案：简化代码并消除冗余

> **[已完成 —— 部分内容已过时]** 已实施并合并。辅助函数提取表参考了重构前的函数名（`_build_file_entities`、`_build_module_files`、`_build_module_entity_map`、`_collect_page_context`、`_prepend_architecture_diagram`），这些函数随后已被 `FileAnalysis` 单次扫描重构和阶段 7 的移除所取代。代码库中已不再存在这些具体的辅助函数名。

## 背景

代码库在 Phase 1 和 Phase 2 过程中积累了大量重复代码。最大的问题在于 `worker/jobs.py` —— 两个长达 400 行的编排函数（`run_full_index`、`run_refresh_index`）共享几乎相同的流水线阶段。本方案旨在提取阶段级辅助函数、去重 LLM 提供商逻辑、简化 API 队列、提取前端工具函数，并对 Docker 配置应用 DRY 原则。

---

## 1. 将 `worker/jobs.py` 分解为阶段辅助函数

在模块级别提取 7 个私有辅助函数。两个编排函数都将调用它们 —— 每个编排函数将变成可读性更强的 ~120 行流程，而非原本 400 行的庞然大物。

### 提取的辅助函数

| 辅助函数 | 取代内容 |
|---|---|
| `_make_on_retry(db_path, job_id)` | 两个任务函数中重复的 `_on_retry` 闭包 |
| `_build_file_entities(files, clone_root)` | 阶段 2 中重复的单文件 AST 实体处理循环 |
| `_build_module_files(module_tree, clone_root)` | 阶段 2b 中重复的模块→文件路径字典构建循环 |
| `_build_module_entity_map(enhanced_tree, file_entities)` | 实体映射构建循环（全量索引版本是权威版本，包含文件/行号增强） |
| `_collect_page_context(page_spec, module_entity_map, dep_summary)` | 阶段 5 中重复的页面实体 + 依赖信息收集循环 |
| `_prepend_architecture_diagram(content, diagram)` | 阶段 6 中重复的基于正则的 Mermaid 图表前置逻辑 |
| `_make_faiss_store(repo_data_dir, embedding)` | 阶段 3 中重复的 4 行 FAISSStore 构造代码 |

### 结果

`run_full_index`：~383 行 → ~150 行
`run_refresh_index`：~462 行 → ~160 行
整个文件：~880 行 → ~660 行

---

## 2. 消除 LLM 提供商 JSON 解析中的重复代码

在 `worker/llm/base.py` 中添加了 `_parse_json_response(raw: str) -> dict`。它会去除可选的 Markdown 代码块标记（` ```json ` / ` ``` `）并调用 `json.loads`。

取代了以下提供商 `generate_structured` 方法中完全相同的 6 行标记去除代码：
- `worker/llm/anthropic_provider.py`
- `worker/llm/openai_provider.py`
- `worker/llm/ollama_provider.py`

---

## 3. 消除 `api/queue.py` 中 Redis 连接池的重复代码

提取了 `_enqueue(job_name, **kwargs)` 辅助函数，处理 `create_pool` / `enqueue_job` / `close` 逻辑。`enqueue_full_index` 和 `enqueue_refresh_index` 都变成了 2 行的封装函数。

---

## 4. 前端：提取 `repoId` 工具函数

在 `web/lib/utils.ts` 中添加了 `repoId(owner, repo)`。取代了 5 个路由文件中内联的 `crypto.createHash(...)` SHA-256 哈希计算：

- `web/app/[owner]/[repo]/layout.tsx`
- `web/app/[owner]/[repo]/page.tsx`
- `web/app/[owner]/[repo]/chat/page.tsx`
- `web/app/[owner]/[repo]/graph/page.tsx`
- `web/app/[owner]/[repo]/[slug]/page.tsx`

---

## 5. `docker-compose.yml` —— 使用 YAML 锚点处理共享环境变量

使用 YAML 扩展字段 (`x-common-env: &common-env`) 来实现 `api` 和 `worker` 服务之间共享的 13 个相同环境变量定义的 DRY 化。

---

## 已修改文件

- `worker/jobs.py`
- `worker/llm/base.py`
- `worker/llm/anthropic_provider.py`
- `worker/llm/openai_provider.py`
- `worker/llm/ollama_provider.py`
- `api/queue.py`
- `web/lib/utils.ts`
- `web/app/[owner]/[repo]/layout.tsx`
- `web/app/[owner]/[repo]/page.tsx`
- `web/app/[owner]/[repo]/chat/page.tsx`
- `web/app/[owner]/[repo]/graph/page.tsx`
- `web/app/[owner]/[repo]/[slug]/page.tsx`
- `docker-compose.yml`

---

## 验证

```bash
uv run ruff check . && uv run ruff format --check .
pytest tests/ --ignore=tests/e2e   # 127 个测试通过
cd web && npm run lint
docker compose config               # 有效的 YAML
```
