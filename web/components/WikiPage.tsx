"use client";

import { useEffect, useRef, useId } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github-dark.css";
import type { Components } from "react-markdown";

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

const slugify = (text: string) =>
  text
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/\s+/g, "-");

const components: Components = {
  h2({ children }) {
    const text = String(children);
    const id = slugify(text);
    return <h2 id={id} className="text-2xl font-semibold mt-8 mb-4 border-b pb-2">{children}</h2>;
  },
  h3({ children }) {
    const text = String(children);
    const id = slugify(text);
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
};

interface Props { title: string; content: string }

export function WikiPageContent({ title, content }: Props) {
  return (
    <article className="max-w-4xl p-8 text-foreground">
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
