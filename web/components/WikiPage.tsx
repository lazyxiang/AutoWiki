"use client";

import { useEffect, useRef, useId } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github-dark.css";
import type { Components } from "react-markdown";

/**
 * Renders a Mermaid diagram using the mermaid.js library.
 * 
 * @param children - The Mermaid diagram definition as a string.
 */
function MermaidBlock({ children }: { children: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useId().replace(/:/g, "_");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const mermaid = (await import("mermaid")).default;
      mermaid.initialize({
        startOnLoad: false,
        theme: "dark",
        securityLevel: "strict",
      });
      if (cancelled || !ref.current) return;
      try {
        const { svg } = await mermaid.render(`mermaid${id}`, children.trim());
        if (!cancelled && ref.current) {
          ref.current.innerHTML = svg;
        }
      } catch {
        // Render as plain code on Mermaid syntax error
        if (ref.current) {
          ref.current.textContent = children;
        }
      }
    })();
    return () => { cancelled = true; };
  }, [children, id]);

  return (
    <div ref={ref} className="my-4 flex justify-center overflow-x-auto" />
  );
}

/**
 * Converts a string into a URL-friendly slug.
 * 
 * @param text - The string to slugify.
 * @returns A slugified version of the string.
 */
const slugify = (text: string) => {
  const slug = text
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");
  return slug || "section";
};

/**
 * Markdown components configuration for ReactMarkdown.
 */
const getComponents = (getUniqueId: (text: string) => string): Components => ({
  h2({ children }) {
    const text = String(children);
    const id = getUniqueId(text);
    return <h2 id={id} className="text-2xl font-semibold mt-8 mb-4 border-b pb-2">{children}</h2>;
  },
  h3({ children }) {
    const text = String(children);
    const id = getUniqueId(text);
    return <h3 id={id} className="text-xl font-semibold mt-6 mb-3">{children}</h3>;
  },
  code({ className, children, ...props }) {
    const match = /language-(\w+)/.exec(className || "");
    const lang = match?.[1];
    const text = String(children).replace(/\n$/, "");

    if (lang === "mermaid") {
      return <MermaidBlock>{text}</MermaidBlock>;
    }

    // Inline code (no language class)
    if (!className) {
      return <code className="bg-muted px-1.5 py-0.5 rounded text-sm" {...props}>{children}</code>;
    }

    // Block code — let rehype-highlight handle syntax coloring
    return <code className={className} {...props}>{children}</code>;
  },
});

/**
 * Props for the WikiPageContent component.
 */
interface Props {
  /** The title of the wiki page. */
  title: string;
  /** The Markdown content of the wiki page. */
  content: string;
}

/**
 * Renders the content of a wiki page, including Markdown and Mermaid diagrams.
 * Automatically generates unique IDs for headings to support Table of Contents.
 */
export function WikiPageContent({ title, content }: Props) {
  // Use a local object for ID tracking within a single render pass.
  // This is safe because we want deterministic IDs for the current content.
  const idCounts: Record<string, number> = {};

  /**
   * Generates a unique ID for a heading.
   */
  const getUniqueId = (text: string) => {
    const baseId = slugify(text);
    const count = idCounts[baseId] || 0;
    idCounts[baseId] = count + 1;
    return count === 0 ? baseId : `${baseId}-${count}`;
  };

  const components = getComponents(getUniqueId);

  return (
    <article className="w-full max-w-4xl p-8 text-foreground mx-auto">
      <h1 className="text-3xl font-bold mb-6">{title}</h1>
      <div className="wiki-content">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[rehypeHighlight]}
          components={components}
        >
          {content}
        </ReactMarkdown>
      </div>
    </article>
  );
}
