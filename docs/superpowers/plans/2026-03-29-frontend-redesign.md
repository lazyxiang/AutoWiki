# AutoWiki Frontend Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replicate the clean, modern, light-mode-first visual style of `deepwiki.com` and `zread.ai` across the AutoWiki Home Page and Wiki Page, integrating search, repository metadata, and a three-column navigation layout.

**Architecture:** 
- **Global Theme:** Light-mode-first using Tailwind v4 CSS variables.
- **Home Page:** Search-centric hero section + responsive grid of repository cards with rich metadata.
- **Wiki Page:** Three-column layout (Sidebar navigation, Constrained-width main content, Sticky Table of Contents).
- **Interactive Features:** Global Chat drawer/FAB, ReactFlow-based dependency graph, and seamless index refresh.

**Tech Stack:** Next.js 16, React 19, Tailwind CSS v4, Lucide React icons, ReactFlow v12 (@xyflow/react), Mermaid.js.

---

## File Structure

### New Components
| File | Responsibility |
|---|---|
| `web/components/RepoCard.tsx` | Displays repo name, description, stars, language, and last updated time. |
| `web/components/TableOfContents.tsx` | Dynamically generated intra-page navigation from Markdown headers. |
| `web/components/RefreshButton.tsx` | Subtle UI for triggering and monitoring repository refresh jobs. |
| `web/components/ChatDrawer.tsx` | Collapsible right-side panel for RAG-powered Q&A. |

### Modified Layouts/Pages
| File | What Changes |
|---|---|
| `web/app/globals.css` | Update OKLCH color variables to prefer light mode and indigo accents. |
| `web/app/layout.tsx` | Remove forced `dark` class; set up global font and background. |
| `web/app/page.tsx` | Redesign as a search-centric hero + repository grid. |
| `web/app/[owner]/[repo]/layout.tsx` | Implement the three-column layout shell. |
| `web/app/[owner]/[repo]/[slug]/page.tsx` | Assemble the Wiki page with TOC integration. |

---

## Task 1: Global Theme & Base Layout

**Files:**
- Modify: `web/app/globals.css`
- Modify: `web/app/layout.tsx`

- [ ] **Step 1: Update `web/app/globals.css` for Light Mode**
Change `:root` variables to use Indigo accents and ensure high contrast.

```css
:root {
  --background: oklch(1 0 0);
  --foreground: oklch(0.15 0.02 260); /* Deep slate/blue-black */
  --card: oklch(1 0 0);
  --card-foreground: oklch(0.15 0.02 260);
  --popover: oklch(1 0 0);
  --popover-foreground: oklch(0.15 0.02 260);
  --primary: oklch(0.55 0.20 260); /* Modern Indigo */
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

- [ ] **Step 2: Update `web/app/layout.tsx`**
Remove the `dark` class from the `html` element to enable light mode by default.

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

- [ ] **Step 3: Commit**
```bash
git add web/app/globals.css web/app/layout.tsx
git commit -m "style: set light mode as default and update global Indigo theme"
```

---

## Task 2: Repository Card Component

**Files:**
- Create: `web/components/RepoCard.tsx`
- Modify: `web/lib/api.ts` (Ensure Repository model includes metadata)

- [ ] **Step 1: Define RepoCard Component**
Create a card that displays rich metadata (stars, language, updated time).

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
        {description || "No description provided."}
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

- [ ] **Step 2: Commit**
```bash
git add web/components/RepoCard.tsx
git commit -m "feat: add rich Repository Card component"
```

---

## Task 3: Home Page Redesign

**Files:**
- Modify: `web/app/page.tsx`
- Modify: `web/components/IndexForm.tsx`

- [ ] **Step 1: Redesign `web/app/page.tsx`**
Implement the centered hero search and the 20-repo grid.

```typescript
import { RepoCard } from "@/components/RepoCard";
import { IndexForm } from "@/components/IndexForm";
import { getRepositories } from "@/lib/api";

export default async function HomePage() {
  const repos = await getRepositories(); // Assume sorted by indexed_at desc

  return (
    <main className="min-h-screen bg-background">
      {/* Hero Section */}
      <section className="pt-24 pb-16 px-6 text-center border-b border-dashed">
        <h1 className="text-5xl font-extrabold tracking-tight text-foreground">
          Explore Open Source Knowledge
        </h1>
        <p className="mt-4 text-xl text-muted-foreground max-w-2xl mx-auto">
          AI-powered wiki generator for any GitHub repository. Search for a repo or paste a link to get started.
        </p>
        <div className="mt-10 max-w-xl mx-auto">
          <IndexForm />
        </div>
      </section>

      {/* Grid Section */}
      <section className="max-w-7xl mx-auto px-6 py-16">
        <h2 className="text-2xl font-bold mb-8">Recently Indexed</h2>
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

- [ ] **Step 2: Update `web/components/IndexForm.tsx`**
Style the search bar to be larger, white-backgrounded, and more prominent.

- [ ] **Step 3: Commit**
```bash
git add web/app/page.tsx web/components/IndexForm.tsx
git commit -m "feat: redesign home page with hero search and repo grid"
```

---

## Task 4: Wiki Three-Column Layout

**Files:**
- Modify: `web/app/[owner]/[repo]/layout.tsx`
- Create: `web/components/TableOfContents.tsx`
- Create: `web/components/RefreshButton.tsx`

- [ ] **Step 1: Implement `web/app/[owner]/[repo]/layout.tsx`**
Set up the Sidebar (left), Main (center), and TOC (right) grid.

```typescript
export default function WikiLayout({ children, params }: { children: React.ReactNode, params: any }) {
  return (
    <div className="flex min-h-screen bg-background">
      {/* Left Column: Sidebar */}
      <aside className="w-72 border-r sticky top-0 h-screen overflow-y-auto hidden lg:block bg-slate-50/50">
        <div className="p-6">
           <div className="flex items-center justify-between mb-8">
              <h2 className="font-bold truncate">{params.repo}</h2>
              <RefreshButton repoId={...} />
           </div>
           <WikiSidebar repoId={...} />
        </div>
      </aside>

      {/* Center & Right Column */}
      <main className="flex-1 flex flex-col lg:flex-row">
        <div className="flex-1 px-6 py-12 max-w-4xl mx-auto">
          {children}
        </div>
        
        {/* Right Column: TOC */}
        <aside className="w-64 sticky top-0 h-screen py-12 px-6 hidden xl:block">
           <TableOfContents />
        </aside>
      </main>
    </div>
  );
}
```

- [ ] **Step 2: Create `web/components/TableOfContents.tsx`**
Extract headers from the DOM (or pass them via state) to build the TOC.

- [ ] **Step 3: Commit**
```bash
git add web/app/[owner]/[repo]/layout.tsx web/components/TableOfContents.tsx
git commit -m "feat: implement three-column wiki layout"
```

---

## Task 5: Chat Drawer & Dependency Graph

**Files:**
- Create: `web/components/ChatDrawer.tsx`
- Modify: `web/components/DependencyGraph.tsx`
- Modify: `web/app/[owner]/[repo]/layout.tsx`

- [ ] **Step 1: Implement ChatDrawer**
A collapsible panel that slides out from the right, replacing the TOC view temporarily.

- [ ] **Step 2: Redesign DependencyGraph**
Use a light theme for `reactflow` and Indigo-themed nodes.

- [ ] **Step 3: Commit**
```bash
git add web/components/ChatDrawer.tsx web/components/DependencyGraph.tsx
git commit -m "feat: add global Chat drawer and update Dependency Graph styling"
```

---

## Task 6: Final Verification

- [ ] **Step 1: Run build**
```bash
cd web && npm run build
```

- [ ] **Step 2: Check responsiveness**
Verify that the three-column layout collapses gracefully to a single column on mobile.

- [ ] **Step 3: Final Commit**
```bash
git commit --allow-empty -m "chore: finalize frontend redesign"
```
