"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { RefreshButton } from "./RefreshButton";

interface Page { slug: string; title: string; parent_slug: string | null }
interface Props { pages: Page[]; owner: string; repo: string; repoId: string }

export function WikiSidebar({ pages, owner, repo, repoId }: Props) {
  const pathname = usePathname();

  return (
    <nav className="w-64 shrink-0 border-r h-full overflow-y-auto p-4 bg-sidebar">
      <div className="flex items-center justify-between mb-4">
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
          {owner}/{repo}
        </p>
        <RefreshButton owner={owner} repo={repo} repoId={repoId} />
      </div>
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
