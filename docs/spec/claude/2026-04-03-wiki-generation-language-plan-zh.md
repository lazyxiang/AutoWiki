# 方案：Wiki 生成语言功能 (EN/ZH)

> **[已完成 —— 部分内容已过时]** 已实施并合并。然而，**步骤 4 和步骤 5** 中提到的 `synthesize_diagrams()`（阶段 7）和 `diagram_synthesis.py` 随后在 Wiki 规划器改进工作（PR #17）中被移除。因此，针对图表阶段的语言指令不再适用；`diagram_synthesis.py` 已不存在。所有其他步骤（数据库列、`language.py` 辅助模块、API 转发、规划器/页面生成器串联、前端实现）均已正确实施。

## 背景

AutoWiki 目前生成的 Wiki 内容均为英文，且没有任何语言配置选项。用户希望：
1. 在主页右上角提供语言切换器（EN/中文），默认为英文。
2. 以所选语言生成 Wiki 内容。
3. 持久化所选语言，并在 RepoCard（仓库卡片）上显示。

方案：将 `wiki_language` 参数从前端通过 API → 队列 → Worker 流水线进行传递，并在 LLM 提示词中附加语言指令。无需完整的 i18n（国际化）框架 —— UI 保持英文，仅改变 *生成的 Wiki 内容* 的语言。

---

## 步骤 1：数据库 —— 增加 `wiki_language` 列

**涉及文件：** `shared/models.py`, `shared/database.py`

- 在 `Repository` 模型中增加 `wiki_language: Mapped[str | None] = mapped_column(String, nullable=True)` 字段（位于 `language` 字段之后）。
- 按照现有模式在 `_apply_migrations()` 中添加迁移逻辑：
  ```python
  if "wiki_language" not in columns:
      connection.execute(text("ALTER TABLE repositories ADD COLUMN wiki_language VARCHAR"))
  ```
- 在应用层将所有 `None` 或缺失值默认设为 `"en"`。

## 步骤 2：语言指令辅助模块

**新文件：** `worker/pipeline/language.py`

一个小模块，包含一个将语言代码映射到提示词后缀的字典：
- `"en"` → 空字符串（无额外指令）。
- `"zh"` → 指令要求 LLM 使用简体中文编写，并保持代码标识符/路径/URL 为英文。

包含两个变体函数：
- `get_language_instruction(lang)` —— 用于页面生成器（正文编写）。
- `get_planner_language_instruction(lang)` —— 用于 Wiki 规划器（规定标题/目的使用目标语言，JSON 键和文件路径保持英文）。

## 步骤 3：后端 API —— 接收并转发 `wiki_language`

**涉及文件：** `api/routers/repos.py`

- 扩展 `IndexRequest`，增加 `wiki_language: str = "en"`。
- `submit_repo()`：在新仓库中存储 `wiki_language`；在重新提交时更新现有仓库的该字段。
- `enqueue_full_index(...)`：传递 `wiki_language` 参数。
- `list_repos()` 和 `get_repo()`：在响应中包含 `wiki_language`（如果为 NULL 则默认为 `"en"`）。
- `refresh_repo()`：从数据库中读取存储的 `wiki_language`，并传递给 `enqueue_refresh_index`。

**涉及文件：** `api/queue.py`

- 为 `enqueue_full_index` 和 `enqueue_refresh_index` 添加 `wiki_language: str = "en"` 参数，并转发给 `_enqueue`。

## 步骤 4：Worker —— 在流水线中传递 `wiki_language`

**涉及文件：** `worker/jobs.py`

- 在 `run_full_index()` 和 `run_refresh_index()` 签名中增加 `wiki_language: str = "en"`。
- 将 `wiki_language` 传递给：
  - `generate_wiki_plan(..., wiki_language=wiki_language)`（阶段 5）
  - `generate_page(..., wiki_language=wiki_language)`（阶段 6，在循环中）
  - `synthesize_diagrams(..., wiki_language=wiki_language)`（阶段 7）
- 当 `run_refresh_index` 回退到 `run_full_index` 时，透传 `wiki_language`。

## 步骤 5：流水线阶段 —— 在提示词中注入语言指令

**涉及文件：** `worker/pipeline/wiki_planner.py`
- 为 `generate_wiki_plan()` 增加 `wiki_language: str = "en"`。
- 构建 `system = _SYSTEM + get_planner_language_instruction(wiki_language)`，并在 `llm.generate_structured()` 调用中使用。

**涉及文件：** `worker/pipeline/page_generator.py`
- 为 `generate_page()` 增加 `wiki_language: str = "en"`。
- 构建 `system = _SYSTEM + get_language_instruction(wiki_language)`，并在 LLM 调用中使用。

**涉及文件：** `worker/pipeline/diagram_synthesis.py`
- 为 `synthesize_diagrams()` 增加 `wiki_language: str = "en"`。
- 保持 Mermaid 节点标签为英文（CJK 字符可能导致 Mermaid 渲染问题），但如果 `wiki_language != "en"`，则附加一条简短指令要求使用目标语言的注释。

## 步骤 6：前端 API 客户端

**涉及文件：** `web/lib/api.ts`

- `submitRepo(url, wikiLanguage)`：发送 `{ url, wiki_language: wikiLanguage }`。
- 在 `Repository` 和 `RepoRaw` 接口中添加 `wiki_language: string`。
- 在 `getRepo()` 和 `getRepositories()` 中映射 `wiki_language`（默认为 `"en"`）。

## 步骤 7：前端 —— 语言切换器 + HeroSection

**新文件：** `web/components/LanguageSwitcher.tsx`
- 一个胶囊状的两段式切换器："EN" / "中文"。
- 激活状态：`bg-primary text-primary-foreground`；非激活状态：`bg-muted text-muted-foreground`。
- 使用来自 lucide-react 的 Globe 图标。
- 接收 `value` 和 `onChange` 作为 Props。

**新文件：** `web/components/HeroSection.tsx`
- 包装 Hero 部分的客户端组件（目前该部分内联在 `page.tsx` 中）。
- 管理 `wikiLanguage` 状态（默认为 `"en"`）。
- 在右上角渲染 `LanguageSwitcher`（绝对定位或 flex-end 布局）。
- 渲染带有 `wikiLanguage` prop 的 `IndexForm`。
- 保留现有的 Hero 文本（h1, subtitle）。

## 步骤 8：前端 —— 更新现有组件

**涉及文件：** `web/components/IndexForm.tsx`
- 接收 `wikiLanguage?: string` prop。
- 传递给 `submitRepo(url, wikiLanguage)`。

**涉及文件：** `web/components/RepoCard.tsx`
- 接收 `wikiLanguage?: string` prop。
- 在元数据行显示 Globe 图标 + "中文" / "EN" 徽章。

**涉及文件：** `web/app/page.tsx`
- 使用 `<HeroSection />` 替换内联的 Hero `<section>`。
- 将 `repo.wiki_language` 传递给每个 `<RepoCard>`。

## 步骤 9：测试

- API 测试：验证 `POST /api/repos` 能够接收/存储 `wiki_language`，且响应中包含该字段。
- 流水线测试：验证当 `wiki_language="zh"` 时提示词包含语言指令，而当为 `"en"` 时则不包含。
- 迁移测试：验证 `_apply_migrations` 正确添加了 `wiki_language` 列。
- 前端测试：验证 `submitRepo` 在请求体中发送了 `wiki_language`。

---

## 验证

1. 启动服务：`docker-compose up` 或 `autowiki serve`。
2. 打开首页 —— 验证右上角可见语言切换器，默认为 "EN"。
3. 切换到 "中文"，提交一个仓库 URL。
4. 监控任务进度 —— 应能正常完成。
5. 查看生成的 Wiki —— 内容应为中文，代码术语保持英文。
6. 检查首页 RepoCard —— 应显示 "中文" 徽章。
7. 刷新该仓库 —— 应以中文（继承的语言）重新生成。
8. 运行：`pytest tests/ --ignore=tests/e2e` 和 `npm test --prefix web`。
9. 运行：`uv run ruff check .` 和 `npm run lint --prefix web`。

## 关键文件

| 文件 | 修改内容 |
|------|--------|
| `shared/models.py` | 增加 `wiki_language` 列 |
| `shared/database.py` | 增加 `wiki_language` 迁移逻辑 |
| `worker/pipeline/language.py` | **新增** —— 语言指令辅助模块 |
| `api/routers/repos.py` | 接收、存储、返回、转发 `wiki_language` |
| `api/queue.py` | 向 ARQ 任务传递 `wiki_language` |
| `worker/jobs.py` | 将 `wiki_language` 串联至流水线阶段 |
| `worker/pipeline/wiki_planner.py` | 在系统提示词中注入语言指令 |
| `worker/pipeline/page_generator.py` | 在系统提示词中注入语言指令 |
| `worker/pipeline/diagram_synthesis.py` | 为图表提供简短语言指令 |
| `web/lib/api.ts` | 发送/接收 `wiki_language` |
| `web/components/LanguageSwitcher.tsx` | **新增** —— EN/中文 切换器 |
| `web/components/HeroSection.tsx` | **新增** —— 带有语言状态的客户端包装器 |
| `web/components/IndexForm.tsx` | 接收并转发 `wikiLanguage` |
| `web/components/RepoCard.tsx` | 显示 Wiki 语言徽章 |
| `web/app/page.tsx` | 使用 HeroSection，向 RepoCard 传递 `wiki_language` |
