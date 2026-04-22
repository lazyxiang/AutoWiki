"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn, repoPath } from "@/lib/utils";
import { RefreshButton } from "./RefreshButton";

interface Page {
  slug: string;
  title: string;
  parent_slug: string | null;
  has_user_notes?: boolean;
}

interface Props {
  pages: Page[];
  owner: string;
  repo: string;
  repoId: string;
  lastCommit?: string;
  indexedAt?: string;
}

function formatIndexedAt(isoDate: string, sha: string): string | null {
  if (!isoDate) return null;
  const date = new Date(isoDate);
  if (Number.isNaN(date.getTime())) return null;
  const formatted = date.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
  const short = sha ? ` (${sha.slice(0, 7)})` : "";
  return `Last indexed: ${formatted}${short}`;
}

export function WikiSidebar({ pages, owner, repo, repoId, lastCommit = "", indexedAt = "" }: Props) {
  const pathname = usePathname();
  const basePath = repoPath(owner, repo, repoId);
  const indexedLine = formatIndexedAt(indexedAt, lastCommit);

  return (
    <nav className="w-64 shrink-0 border-r h-full overflow-y-auto p-4 bg-slate-50/50">
      <div className="flex items-center justify-between mb-1">
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider truncate mr-2">
          {owner}/{repo}
        </p>
        <RefreshButton owner={owner} repo={repo} repoId={repoId} />
      </div>
      {indexedLine && (
        <p className="text-foreground pb-1 text-xs">{indexedLine}</p>
      )}
      <div className="mt-3" />
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
            href={`${basePath}/research`}
            className={cn(
              "block text-sm px-2 py-1.5 rounded-lg hover:bg-slate-200/50 transition-colors",
              pathname === `${basePath}/research` && "bg-slate-200/50 font-medium text-primary"
            )}
          >
            Research
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
              {page.has_user_notes && (
                <>
                  <span
                    aria-hidden="true"
                    className="ml-1 text-xs text-blue-500"
                    title="Steered by .autowiki/wiki.json"
                  >
                    ●
                  </span>
                  <span className="sr-only">Steered by .autowiki/wiki.json</span>
                </>
              )}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
