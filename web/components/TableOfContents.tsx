"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

interface Heading {
  id: string;
  text: string;
  level: number;
}

export function TableOfContents() {
  const [headings, setHeadings] = useState<Heading[]>([]);
  const pathname = usePathname();

  useEffect(() => {
    // Small delay to ensure ReactMarkdown has finished rendering
    const timer = setTimeout(() => {
      const article = document.querySelector("article");
      if (!article) return;

      const items = Array.from(article.querySelectorAll("h2, h3")).map((el) => ({
        id: el.id,
        text: el.textContent || "",
        level: parseInt(el.tagName.replace("H", ""), 10),
      }));
      setHeadings(items);
    }, 100);

    return () => clearTimeout(timer);
  }, [pathname]);

  if (headings.length === 0) return null;

  return (
    <aside className="w-64 shrink-0 hidden xl:block h-full overflow-y-auto p-4 border-l">
      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-4">
        On this page
      </p>
      <ul className="space-y-2">
        {headings.map((heading) => (
          <li
            key={heading.id}
            style={{ paddingLeft: `${(heading.level - 2) * 1}rem` }}
          >
            <a
              href={`#${heading.id}`}
              className={cn(
                "block text-sm text-muted-foreground hover:text-foreground transition-colors",
                heading.level === 2 ? "font-medium" : "text-xs"
              )}
            >
              {heading.text}
            </a>
          </li>
        ))}
      </ul>
    </aside>
  );
}
