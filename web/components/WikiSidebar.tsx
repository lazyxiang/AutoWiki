"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

interface Page { slug: string; title: string; parent_slug: string | null }
interface Props { pages: Page[]; owner: string; repo: string }

export function WikiSidebar({ pages, owner, repo }: Props) {
  const pathname = usePathname();
  const topLevel = pages.filter(p => !p.parent_slug);

  return (
    <nav className="w-64 shrink-0 border-r h-full overflow-y-auto p-4">
      <p className="text-xs font-semibold text-muted-foreground uppercase mb-3">{owner}/{repo}</p>
      <ul className="space-y-1">
        {topLevel.map(page => (
          <li key={page.slug}>
            <Link
              href={`/${owner}/${repo}/${page.slug}`}
              className={cn(
                "block text-sm px-2 py-1 rounded hover:bg-accent",
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
