# Wiki 质量与导航改进实施方案

> **[已完成]** 已实施并合并。`Job` 上的 `status_description`、具有父级 slug 导航的分层侧边栏以及 Markdown CSS 文章样式均已就绪。

> **对于智能 Agent：** 必须使用的子技能：建议使用 superpowers:subagent-driven-development 或 superpowers:executing-plans 来逐项任务实施此方案。步骤使用复选框 (`- [ ]`) 语法进行跟踪。

**目标：** 通过提供详细的状态描述、实现具有智能“概览”重定向的分层侧边栏以及修复 Markdown 渲染样式，增强 Wiki 生成的用户体验（UX）。

**架构：**
- 扩展 `Job` 模型，增加 `status_description`，并在 Worker 和 API 中进行传播。
- 更新前端侧边栏，根据 `parent_slug` 递归渲染页面。
- 为 Markdown 内容添加 CSS 工具层，以恢复因 Tailwind 重置而丢失的格式。

**技术栈：** Python (FastAPI, SQLAlchemy), Next.js (React 19, Tailwind 4), PostgreSQL/SQLite。

---

## 任务

### 任务 1：为 Job 模型增加 status_description

**涉及文件：**
- 修改：`shared/models.py`
- 修改：`shared/database.py`（如果需要进行迁移/初始化）

- [ ] **步骤 1：为 `Job` 模型增加字段**
```python
class Job(Base):
    # ... 现有字段
    status_description: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **步骤 2：必要时更新数据库初始化**
`shared/database.py` 中的 `init_db` 函数使用 `Base.metadata.create_all`，这在开发过程中可能无法处理向现有表添加列的情况。鉴于我们处于开发阶段，可以直接删除并重建，或者手动更新。

- [ ] **步骤 3：提交**
```bash
git add shared/models.py
git commit -m "feat: add status_description field to Job model"
```

### 任务 2：更新 Worker 以报告状态描述

**涉及文件：**
- 修改：`worker/jobs.py`

- [ ] **步骤 1：更新 `run_full_index` 中的 `_update_job` 调用**
向每个 `_update_job` 调用传递 `status_description`。
示例：
- "正在克隆仓库并获取文件..."
- "正在分析源码结构 (AST)..."
- "正在构建依赖图..."
- "正在为 RAG 搜索建立索引..."
- "正在规划 Wiki 结构..."
- "正在生成页面：{title}..."

- [ ] **步骤 2：验证 Worker 更新**
如果可能，运行本地测试或模拟 DB 会话。

- [ ] **步骤 3：提交**
```bash
git add worker/jobs.py
git commit -m "feat: report detailed status descriptions during wiki generation"
```

### 任务 3：更新 API 和 WebSocket 以公开状态描述

**涉及文件：**
- 修改：`api/routers/jobs.py`
- 修改：`api/ws/jobs.py`

- [ ] **步骤 1：更新 Job 响应 Schema**
确保在 Job 响应的 Pydantic 模型中包含 `status_description`。

- [ ] **步骤 2：更新 WebSocket 消息格式**
在进度更新消息中包含 `status_description`。

- [ ] **步骤 3：提交**
```bash
git add api/routers/jobs.py api/ws/jobs.py
git commit -m "feat: expose status_description in API and WebSocket"
```

### 任务 4：在前端显示状态描述

**涉及文件：**
- 修改：`web/components/JobProgressBar.tsx`

- [ ] **步骤 1：更新 `useJobProgress` 钩子或直接状态**
确保前端接收并显示 `status_description`。

- [ ] **步骤 2：更新 UI 布局**
用具体的描述替换或增强通用的 "running..." 文本。

- [ ] **步骤 3：提交**
```bash
git add web/components/JobProgressBar.tsx
git commit -m "feat: display detailed job status in progress bar"
```

### 任务 5：改进 Wiki 导航和重定向

**涉及文件：**
- 修改：`web/app/[owner]/[repo]/page.tsx`

- [ ] **步骤 1：实现“概览”重定向逻辑**
查找 slug 为 `overview` 或标题中包含“Overview”的页面。如果找到则重定向到该页面；否则，重定向到第一个可用页面。

- [ ] **步骤 2：提交**
```bash
git add web/app/[owner]/[repo]/page.tsx
git commit -m "fix: redirect to overview page by default"
```

### 任务 6：分层 Wiki 侧边栏

**涉及文件：**
- 修改：`web/components/WikiSidebar.tsx`

- [ ] **步骤 1：将扁平的页面列表转换为树状结构**
编写一个工具函数，利用 `parent_slug` 从页面列表中构建树。

- [ ] **步骤 2：在 `WikiSidebar` 中实现递归渲染**
为子页面渲染嵌套的 `<ul>` 元素。添加缩进或切换图标。

- [ ] **步骤 3：提交**
```bash
git add web/components/WikiSidebar.tsx
git commit -m "feat: implement hierarchical sidebar navigation"
```

### 任务 7：修复 Markdown 渲染样式

**涉及文件：**
- 修改：`web/app/globals.css`
- 修改：`web/components/WikiPage.tsx`

- [ ] **步骤 1：在 `globals.css` 中添加 `.wiki-content` 样式**
在 `.wiki-content` 容器内为 `h1`, `h2`, `h3`, `p`, `ul`, `ol`, `li`, `blockquote`, `table` 等添加样式。使用 Tailwind 的 `@apply` 或标准 CSS。

```css
.wiki-content {
  @apply leading-relaxed;
}
.wiki-content h1 { @apply text-3xl font-bold mt-8 mb-4 border-b pb-2; }
.wiki-content h2 { @apply text-2xl font-semibold mt-6 mb-3; }
.wiki-content p { @apply my-4; }
.wiki-content ul { @apply list-disc ml-6 my-4; }
/* ... 依此类推 */
```

- [ ] **步骤 2：验证渲染**
手动检查 Wiki 页面的显示效果。

- [ ] **步骤 3：提交**
```bash
git add web/app/globals.css
git commit -m "fix: add base styles for Markdown content rendering"
```
