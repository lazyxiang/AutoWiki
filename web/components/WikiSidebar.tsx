"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { cn, repoId } from "@/lib/utils";
import { refreshRepo } from "@/lib/api";

interface Page { slug: string; title: string; parent_slug: string | null }
interface Props { pages: Page[]; owner: string; repo: string }

export function WikiSidebar({ pages, owner, repo }: Props) {
  const pathname = usePathname();
  const router = useRouter();
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  async function handleRefresh() {
    setRefreshing(true);
    setError("");
    try {
      const { job_id } = await refreshRepo(repoId(owner, repo));
      router.push(`/jobs/${job_id}?repo_id=${repoId(owner, repo)}&owner=${encodeURIComponent(owner)}&repo=${encodeURIComponent(repo)}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Refresh failed");
      setRefreshing(false);
    }
  }

  return (
    <nav className="w-64 shrink-0 border-r h-full overflow-y-auto p-4 bg-sidebar">
      <div className="flex items-center justify-between mb-4">
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
          {owner}/{repo}
        </p>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
          title="Refresh wiki"
        >
          {refreshing ? "…" : "↻"}
        </button>
      </div>
      {error && <p className="text-destructive text-xs mb-2">{error}</p>}
      <ul className="space-y-1 mb-4">
        <li>
          <Link
            href={`/${owner}/${repo}/chat`}
            className={cn(
              "block text-sm px-2 py-1 rounded hover:bg-accent",
              pathname.endsWith("/chat") && "bg-accent font-medium"
            )}
          >
            Chat
          </Link>
        </li>
        <li>
          <Link
            href={`/${owner}/${repo}/graph`}
            className={cn(
              "block text-sm px-2 py-1 rounded hover:bg-accent",
              pathname.endsWith("/graph") && "bg-accent font-medium"
            )}
          >
            Module Graph
          </Link>
        </li>
      </ul>
      <ul className="space-y-1">
        {pages.map(page => (
          <li key={page.slug}>
            <Link
              href={`/${owner}/${repo}/${page.slug}`}
              className={cn(
                "block text-sm px-2 py-1 rounded hover:bg-accent",
                page.parent_slug && "pl-4",
                pathname.endsWith(`/${page.slug}`) && "bg-accent font-medium"
              )}
            >
              {page.title}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
