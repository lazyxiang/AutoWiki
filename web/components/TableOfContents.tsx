"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

/**
 * Represents a single heading item in the Table of Contents.
 */
interface Heading {
  /** The DOM ID of the heading. */
  id: string;
  /** The text content of the heading. */
  text: string;
  /** The heading level (e.g., 2 for H2, 3 for H3). */
  level: number;
}

/**
 * A sticky sidebar component that displays a Table of Contents for the current page.
 * It dynamically extracts headings from the `<article>` element and highlights the active one.
 */
export function TableOfContents() {
  const [headings, setHeadings] = useState<(Heading & { isActive?: boolean })[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const pathname = usePathname();

  useEffect(() => {
    // Small delay to ensure ReactMarkdown has finished rendering the DOM
    const timer = setTimeout(() => {
      const article = document.querySelector("article");
      if (!article) {
        setHeadings([]);
        return;
      }

      const items = Array.from(article.querySelectorAll("h2, h3")).map((el) => ({
        id: el.id,
        text: el.textContent || "",
        level: parseInt(el.tagName.replace("H", ""), 10),
      }));
      setHeadings(items);

      // IntersectionObserver for active heading
      const observer = new IntersectionObserver(
        (entries) => {
          const visibleEntry = entries.find((entry) => entry.isIntersecting);
          if (visibleEntry) {
            setActiveId(visibleEntry.target.id);
          }
        },
        { rootMargin: "-80px 0% -80% 0%" } // Adjust based on header height
      );

      items.forEach((item) => {
        const el = document.getElementById(item.id);
        if (el) observer.observe(el);
      });

      return () => observer.disconnect();
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
        {headings.map((heading) => {
          const isActive = heading.id === activeId;
          return (
            <li
              key={heading.id}
              style={{ paddingLeft: `${(heading.level - 2) * 1}rem` }}
              className={cn(
                "border-l-2 transition-colors",
                isActive ? "border-primary" : "border-transparent"
              )}
            >
              <a
                href={`#${heading.id}`}
                className={cn(
                  "block text-sm py-1 pl-3 transition-colors",
                  isActive ? "text-primary font-medium" : "text-muted-foreground hover:text-foreground",
                  heading.level === 3 && "text-xs"
                )}
              >
                {heading.text}
              </a>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
