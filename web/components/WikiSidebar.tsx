"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { RefreshButton } from "./RefreshButton";

/**
 * Metadata for a single wiki page.
 */
interface Page {
  /** The URL slug of the page. */
  slug: string;
  /** The display title of the page. */
  title: string;
  /** The slug of the parent page, if any. */
  parent_slug: string | null;
}

/**
 * Props for the WikiSidebar component.
 */
interface Props {
  /** List of wiki pages to display. */
  pages: Page[];
  /** The owner of the repository. */
  owner: string;
  /** The name of the repository. */
  repo: string;
  /** The ID of the repository. */
  repoId: string;
}

/**
 * Navigation sidebar for the wiki.
 * Displays structural links to all generated pages and utility links (Chat, Graph).
 */
export function WikiSidebar({ pages, owner, repo, repoId }: Props) {
  const pathname = usePathname();
  const basePath = `/${owner}/${repo}`;

  return (
    <nav className="w-64 shrink-0 border-r h-full overflow-y-auto p-4 bg-slate-50/50">
      <div className="flex items-center justify-between mb-4">
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider truncate mr-2">
          {owner}/{repo}
        </p>
        <RefreshButton owner={owner} repo={repo} repoId={repoId} />
      </div>
      <ul className="space-y-1 mb-4">
        <li>
          <Link
            href={`${basePath}/chat`}
            className={cn(
              "block text-sm px-2 py-1.5 rounded-lg hover:bg-slate-200/50 transition-colors",
              pathname === `${basePath}/chat` && "bg-slate-200/50 font-medium text-primary"
            )}
          >
            Chat
          </Link>
        </li>
        <li>
          <Link
            href={`${basePath}/graph`}
            className={cn(
              "block text-sm px-2 py-1.5 rounded-lg hover:bg-slate-200/50 transition-colors",
              pathname === `${basePath}/graph` && "bg-slate-200/50 font-medium text-primary"
            )}
          >
            Module Graph
          </Link>
        </li>
      </ul>
      <div className="my-4 border-t border-slate-200" />
      <ul className="space-y-1">
        {pages.map(page => (
          <li key={page.slug}>
            <Link
              href={`${basePath}/${page.slug}`}
              className={cn(
                "block text-sm px-2 py-1.5 rounded-lg hover:bg-slate-200/50 transition-colors",
                page.parent_slug && "ml-4",
                pathname === `${basePath}/${page.slug}` && "bg-slate-200/50 font-medium text-primary"
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
