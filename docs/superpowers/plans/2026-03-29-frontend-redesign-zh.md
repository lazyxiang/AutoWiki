# AutoWiki 前端重构实施计划

> **[已完成]** 已实施并合并。技术说明：计划中提到 Next.js 16 / React 19 / Tailwind v4；实际交付的版本是 Next.js 16.2.1, React 19, Tailwind v4（仅 CSS 配置）。使用了 `@base-ui/react` 而不是 `@radix-ui/react`。已采用 ReactFlow v12 (`@xyflow/react`) 和 Mermaid.js。

> **对于智能体工作者：** 要求的子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 来逐项任务实施此计划。步骤使用复选框 (`- [ ]`) 语法进行跟踪。

**目标：** 在 AutoWiki 首页和维基页面中复制 `deepwiki.com` 和 `zread.ai` 简洁、现代、浅色模式优先的视觉风格，集成搜索、仓库元数据和三栏导航布局。

**架构：** 
- **全局主题：** 使用 Tailwind v4 CSS 变量的浅色模式优先。
- **首页：** 以搜索为核心的英雄板块 + 带有丰富元数据的响应式仓库卡片网格。
- **维基页面：** 三栏布局（左侧侧边栏导航、中心限制宽度的主要内容、右侧固定目录）。
- **交互功能：** 全局聊天抽屉/悬浮按钮、基于 ReactFlow 的依赖图，以及无缝的索引刷新。

**技术栈：** Next.js 16, React 19, Tailwind CSS v4, Lucide React 图标, ReactFlow v12 (@xyflow/react), Mermaid.js。

---

## 文件结构

### 新组件
| 文件 | 职责 |
|---|---|
| `web/components/RepoCard.tsx` | 显示仓库名称、描述、星数、语言和最后更新时间。 |
| `web/components/TableOfContents.tsx` | 从 Markdown 标题动态生成的页内导航。 |
| `web/components/RefreshButton.tsx` | 用于触发和监控仓库刷新任务的细微 UI。 |
| `web/components/ChatDrawer.tsx` | 用于 RAG 驱动的问答的可折叠右侧面板。 |

### 修改的布局/页面
| 文件 | 变化内容 |
|---|---|
| `web/app/globals.css` | 更新 OKLCH 颜色变量，首选浅色模式和靛蓝色强调。 |
| `web/app/layout.tsx` | 移除强制的 `dark` 类；设置全局字体和背景。 |
| `web/app/page.tsx` | 重新设计为以搜索为核心的英雄板块 + 仓库网格。 |
| `web/app/[owner]/[repo]/layout.tsx` | 实现三栏布局外壳。 |
| `web/app/[owner]/[repo]/[slug]/page.tsx` | 组装集成目录的维基页面。 |

---

## 任务 1：全局主题与基础布局

**文件：**
- 修改：`web/app/globals.css`
- 修改：`web/app/layout.tsx`

- [ ] **步骤 1：为浅色模式更新 `web/app/globals.css`**
更改 `:root` 变量以使用靛蓝色（Indigo）强调并确保高对比度。

```css
:root {
  --background: oklch(1 0 0);
  --foreground: oklch(0.15 0.02 260); /* 深色板岩/蓝黑 */
  --card: oklch(1 0 0);
  --card-foreground: oklch(0.15 0.02 260);
  --popover: oklch(1 0 0);
  --popover-foreground: oklch(0.15 0.02 260);
  --primary: oklch(0.55 0.20 260); /* 现代靛蓝 */
  --primary-foreground: oklch(0.98 0 0);
  --secondary: oklch(0.96 0.01 260);
  --secondary-foreground: oklch(0.20 0.02 260);
  --muted: oklch(0.96 0.01 260);
  --muted-foreground: oklch(0.45 0.02 260);
  --accent: oklch(0.96 0.01 260);
  --accent-foreground: oklch(0.20 0.02 260);
  --border: oklch(0.92 0.01 260);
  --input: oklch(0.92 0.01 260);
  --ring: oklch(0.55 0.20 260);
  --radius: 0.75rem;
}
```

- [ ] **步骤 2：更新 `web/app/layout.tsx`**
从 `html` 元素中移除 `dark` 类，默认启用浅色模式。

```typescript
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased bg-background text-foreground">
        {children}
      </body>
    </html>
  );
}
```

- [ ] **步骤 3：提交**
```bash
git add web/app/globals.css web/app/layout.tsx
git commit -m "style: set light mode as default and update global Indigo theme"
```

---

## 任务 2：仓库卡片组件

**文件：**
- 创建：`web/components/RepoCard.tsx`
- 修改：`web/lib/api.ts`（确保 Repository 模型包含元数据）

- [ ] **步骤 1：定义 RepoCard 组件**
创建一个显示丰富元数据（星数、语言、更新时间）的卡片。

```typescript
import { Star, Clock, Code2 } from "lucide-react";
import Link from "next/link";

interface RepoCardProps {
  owner: string;
  name: string;
  description: string;
  stars?: number;
  language?: string;
  updatedAt: string;
}

export function RepoCard({ owner, name, description, stars, language, updatedAt }: RepoCardProps) {
  return (
    <Link href={`/${owner}/${name}`} className="group block p-5 bg-card border border-border rounded-xl hover:border-primary/50 hover:shadow-sm transition-all">
      <h3 className="text-lg font-bold group-hover:text-primary transition-colors">
        <span className="text-muted-foreground font-normal">{owner}/</span>{name}
      </h3>
      <p className="mt-2 text-sm text-muted-foreground line-clamp-2 h-10">
        {description || "未提供描述。"}
      </p>
      <div className="mt-4 flex items-center gap-4 text-xs text-muted-foreground">
        {language && (
          <span className="flex items-center gap-1.5">
            <Code2 size={14} className="text-primary" /> {language}
          </span>
        )}
        {stars !== undefined && (
          <span className="flex items-center gap-1.5">
            <Star size={14} className="text-yellow-500 fill-yellow-500" /> {stars.toLocaleString()}
          </span>
        )}
        <span className="flex items-center gap-1.5 ml-auto">
          <Clock size={14} /> {updatedAt}
        </span>
      </div>
    </Link>
  );
}
```

- [ ] **步骤 2：提交**
```bash
git add web/components/RepoCard.tsx
git commit -m "feat: add rich Repository Card component"
```

---

## 任务 3：首页重构

**文件：**
- 修改：`web/app/page.tsx`
- 修改：`web/components/IndexForm.tsx`

- [ ] **步骤 1：重构 `web/app/page.tsx`**
实现居中的英雄板块搜索和 20 个仓库的网格。

```typescript
import { RepoCard } from "@/components/RepoCard";
import { IndexForm } from "@/components/IndexForm";
import { getRepositories } from "@/lib/api";

export default async function HomePage() {
  const repos = await getRepositories(); // 假设按 indexed_at 降序排序

  return (
    <main className="min-h-screen bg-background">
      {/* 英雄板块 */}
      <section className="pt-24 pb-16 px-6 text-center border-b border-dashed">
        <h1 className="text-5xl font-extrabold tracking-tight text-foreground">
          探索开源知识
        </h1>
        <p className="mt-4 text-xl text-muted-foreground max-w-2xl mx-auto">
          为任何 GitHub 仓库提供 AI 驱动的维基生成器。搜索仓库或粘贴链接即可开始。
        </p>
        <div className="mt-10 max-w-xl mx-auto">
          <IndexForm />
        </div>
      </section>

      {/* 网格板块 */}
      <section className="max-w-7xl mx-auto px-6 py-16">
        <h2 className="text-2xl font-bold mb-8">最近索引</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {repos.slice(0, 20).map((repo) => (
            <RepoCard 
              key={repo.id}
              owner={repo.owner}
              name={repo.name}
              description={repo.description}
              stars={repo.stars}
              language={repo.language}
              updatedAt={repo.indexed_at_formatted}
            />
          ))}
        </div>
      </section>
    </main>
  );
}
```

- [ ] **步骤 2：更新 `web/components/IndexForm.tsx`**
将搜索栏样式设置为更大、白色背景且更醒目。

- [ ] **步骤 3：提交**
```bash
git add web/app/page.tsx web/components/IndexForm.tsx
git commit -m "feat: redesign home page with hero search and repo grid"
```

---

## 任务 4：维基三栏布局

**文件：**
- 修改：`web/app/[owner]/[repo]/layout.tsx`
- 创建：`web/components/TableOfContents.tsx`
- 创建：`web/components/RefreshButton.tsx`

- [ ] **步骤 1：实现 `web/app/[owner]/[repo]/layout.tsx`**
设置侧边栏（左）、主要内容（中）和目录（右）网格。

```typescript
export default function WikiLayout({ children, params }: { children: React.ReactNode, params: any }) {
  return (
    <div className="flex min-h-screen bg-background">
      {/* 左栏：侧边栏 */}
      <aside className="w-72 border-r sticky top-0 h-screen overflow-y-auto hidden lg:block bg-slate-50/50">
        <div className="p-6">
           <div className="flex items-center justify-between mb-8">
              <h2 className="font-bold truncate">{params.repo}</h2>
              <RefreshButton repoId={...} />
           </div>
           <WikiSidebar repoId={...} />
        </div>
      </aside>

      {/* 中间和右栏 */}
      <main className="flex-1 flex flex-col lg:flex-row">
        <div className="flex-1 px-6 py-12 max-w-4xl mx-auto">
          {children}
        </div>
        
        {/* 右栏：目录 */}
        <aside className="w-64 sticky top-0 h-screen py-12 px-6 hidden xl:block">
           <TableOfContents />
        </aside>
      </main>
    </div>
  );
}
```

- [ ] **步骤 2：创建 `web/components/TableOfContents.tsx`**
从 DOM 中提取标题（或通过状态传递）来构建目录。

- [ ] **步骤 3：提交**
```bash
git add web/app/[owner]/[repo]/layout.tsx web/components/TableOfContents.tsx
git commit -m "feat: implement three-column wiki layout"
```

---

## 任务 5：聊天抽屉与依赖图

**文件：**
- 创建：`web/components/ChatDrawer.tsx`
- 修改：`web/components/DependencyGraph.tsx`
- 修改：`web/app/[owner]/[repo]/layout.tsx`

- [ ] **步骤 1：实现 ChatDrawer**
一个从右侧滑出的可折叠面板，临时替换目录视图。

- [ ] **步骤 2：重新设计 DependencyGraph**
为 `reactflow` 使用浅色主题和靛蓝色主题节点。

- [ ] **步骤 3：提交**
```bash
git add web/components/ChatDrawer.tsx web/components/DependencyGraph.tsx
git commit -m "feat: add global Chat drawer and update Dependency Graph styling"
```

---

## 任务 6：最终验证

- [ ] **步骤 1：运行构建**
```bash
cd web && npm run build
```

- [ ] **步骤 2：检查响应式设计**
验证三栏布局在移动端能否优雅地折叠为单栏。

- [ ] **步骤 3：最终提交**
```bash
git commit --allow-empty -m "chore: finalize frontend redesign"
```
