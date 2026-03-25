"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

interface Page {
  slug: string;
  title: string;
  parent_slug: string | null;
}
interface Props {
  pages: Page[];
  owner: string;
  repo: string;
}

function PageLink({
  page,
  owner,
  repo,
  pathname,
  level = 0,
  allPages,
}: {
  page: Page;
  owner: string;
  repo: string;
  pathname: string;
  level?: number;
  allPages: Page[];
}) {
  const children = allPages.filter((p) => p.parent_slug === page.slug);
  const isActive = pathname.endsWith(`/${page.slug}`);

  return (
    <li className="space-y-1">
      <Link
        href={`/${owner}/${repo}/${page.slug}`}
        className={cn(
          "block text-sm px-2 py-1 rounded hover:bg-accent transition-colors",
          isActive && "bg-accent font-medium text-accent-foreground",
          level > 0 && "ml-4"
        )}
      >
        {page.title}
      </Link>
      {children.length > 0 && (
        <ul className="space-y-1">
          {children.map((child) => (
            <PageLink
              key={child.slug}
              page={child}
              owner={owner}
              repo={repo}
              pathname={pathname}
              level={level + 1}
              allPages={allPages}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

export function WikiSidebar({ pages, owner, repo }: Props) {
  const pathname = usePathname();
  const topLevel = pages.filter((p) => !p.parent_slug);

  return (
    <nav className="w-64 shrink-0 border-r h-full overflow-y-auto p-4 bg-sidebar">
      <p className="text-xs font-semibold text-muted-foreground uppercase mb-4 tracking-wider">
        {owner}/{repo}
      </p>
      <ul className="space-y-1">
        {topLevel.map((page) => (
          <PageLink
            key={page.slug}
            page={page}
            owner={owner}
            repo={repo}
            pathname={pathname}
            allPages={pages}
          />
        ))}
      </ul>
    </nav>
  );
}
