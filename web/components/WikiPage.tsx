"use client";

import { useEffect, useRef, useId, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github.css";
import type { Components } from "react-markdown";
import { sanitizeMermaid } from "@/lib/mermaid-sanitize";
import { Maximize2, X, ZoomIn, ZoomOut, RotateCcw, Move, Github } from "lucide-react";
import { Button } from "./ui/button";

/**
 * Renders a Mermaid diagram using the mermaid.js library with light theme and interactivity.
 */
function MermaidBlock({ children }: { children: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useId().replace(/:/g, "_");
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [svgContent, setSvgContent] = useState<string>("");
  const [zoom, setZoom] = useState(1);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const mermaid = (await import("mermaid")).default;
      mermaid.initialize({
        startOnLoad: false,
        theme: "default",
        securityLevel: "loose",
        fontFamily: "var(--font-sans)",
        // Attempt to fix truncation by ensuring reasonable default width
        flowchart: { useMaxWidth: false, htmlLabels: true },
        sequence: { useMaxWidth: false },
        gantt: { useMaxWidth: false },
      });
      try {
        const renderId = `mermaid${id}_${Date.now()}`;
        const sanitized = sanitizeMermaid(children.trim());
        const { svg } = await mermaid.render(renderId, sanitized);
        if (!cancelled) {
          // Wrap SVG to ensure it doesn't truncate and supports proper scaling
          const wrappedSvg = svg.replace(/<svg/, '<svg style="max-width: 100%; height: auto;"');
          setSvgContent(wrappedSvg);
          if (ref.current) {
            ref.current.innerHTML = wrappedSvg;
          }
        }
      } catch (e) {
        console.error("Mermaid render error:", e);
        if (ref.current) {
          ref.current.textContent = children;
        }
      }
    })();
    return () => { cancelled = true; };
  }, [children, id]);

  const handleMouseDown = (e: React.MouseEvent) => {
    setIsDragging(true);
    setDragStart({ x: e.clientX - position.x, y: e.clientY - position.y });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isDragging) {
      setPosition({
        x: e.clientX - dragStart.x,
        y: e.clientY - dragStart.y,
      });
    }
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    setZoom(prev => Math.min(Math.max(prev * delta, 0.5), 5));
  };

  return (
    <>
      <div className="group relative my-6">
        <div 
          ref={ref} 
          className="p-4 bg-slate-50 border border-slate-200 rounded-xl flex justify-center overflow-hidden cursor-pointer hover:bg-slate-100/50 transition-colors"
          onClick={() => setIsModalOpen(true)}
        />
        <Button
          variant="outline"
          size="icon-sm"
          className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity bg-white/80 backdrop-blur"
          onClick={(e) => { e.stopPropagation(); setIsModalOpen(true); }}
          title="Maximize Diagram"
        >
          <Maximize2 className="h-4 w-4" />
        </Button>
      </div>

      {isModalOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-8 md:p-12 lg:p-16">
          {/* Blurred Background Overlay */}
          <div 
            className="absolute inset-0 bg-slate-900/40 backdrop-blur-md transition-opacity"
            onClick={() => setIsModalOpen(false)}
          />
          
          {/* Modal Container */}
          <div className="relative w-full h-full bg-white rounded-2xl shadow-2xl overflow-hidden flex flex-col border border-slate-200">
            {/* Controls Overlay (Inside Canvas) */}
            <div className="absolute top-4 right-4 z-10 flex items-center gap-2 bg-white/80 backdrop-blur p-1.5 rounded-xl border border-slate-200 shadow-sm">
              <Button variant="ghost" size="icon-sm" onClick={() => setZoom(prev => prev * 1.2)} title="Zoom In">
                <ZoomIn className="h-4 w-4" />
              </Button>
              <Button variant="ghost" size="icon-sm" onClick={() => setZoom(prev => prev * 0.8)} title="Zoom Out">
                <ZoomOut className="h-4 w-4" />
              </Button>
              <Button variant="ghost" size="icon-sm" onClick={() => { setZoom(1); setPosition({ x: 0, y: 0 }); }} title="Reset">
                <RotateCcw className="h-4 w-4" />
              </Button>
              <div className="w-px h-4 bg-slate-200 mx-1" />
              <Button variant="ghost" size="icon-sm" onClick={() => setIsModalOpen(false)} title="Close">
                <X className="h-5 w-5" />
              </Button>
            </div>

            {/* Modal Body (Interactive Area) */}
            <div 
              className="flex-1 relative bg-white cursor-grab active:cursor-grabbing overflow-hidden"
              onMouseDown={handleMouseDown}
              onMouseMove={handleMouseMove}
              onMouseUp={handleMouseUp}
              onMouseLeave={handleMouseUp}
              onWheel={handleWheel}
            >
              <div 
                className="absolute inset-0 flex items-center justify-center p-12 transition-transform duration-75"
                style={{ 
                  transform: `translate(${position.x}px, ${position.y}px) scale(${zoom})`,
                }}
                dangerouslySetInnerHTML={{ __html: svgContent }}
              />
              
              {/* Legend/Help */}
              <div className="absolute bottom-4 left-4 flex items-center gap-2 text-[10px] text-muted-foreground bg-white/60 backdrop-blur px-2.5 py-1 rounded-full border border-slate-200">
                <Move className="h-3 w-3" /> Drag to move • Scroll to zoom
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/**
 * Renders a styled badge for source code citations with a link to GitHub.
 */
function SourceBadge({ path, lines, owner, repo }: { path: string; lines: string; owner: string; repo: string }) {
  // Clean lines to ensure we only have digits and dashes for the anchor
  // GitHub anchors for ranges look like #L10-L20
  const cleanLines = lines.replace(/[^0-9-]/g, "");
  const lineAnchor = cleanLines ? `#L${cleanLines.replace("-", "-L")}` : "";
  const githubUrl = `https://github.com/${owner}/${repo}/blob/main/${path}${lineAnchor}`;

  return (
    <a
      href={githubUrl}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center rounded-md overflow-hidden border border-slate-200/60 bg-slate-50 hover:bg-slate-100 transition-all no-underline group shadow-sm shrink-0"
    >
      <div className="flex items-center gap-2.5 px-3 py-1.5 text-[13px] font-mono text-slate-700">
        <Github className="h-3.5 w-3.5 text-slate-400 group-hover:text-slate-900 transition-colors" />
        <span className="truncate max-w-[150px] md:max-w-md">{path}</span>
      </div>
      {cleanLines && (
        <div className="px-2.5 py-1 bg-slate-200/40 text-[13px] font-mono text-slate-500 border-l border-slate-200/60 group-hover:bg-slate-200/60 transition-colors h-full flex items-center">
          {cleanLines}
        </div>
      )}
    </a>
  );
}

/**
 * Checks if a path is likely a file (has a name and an extension).
 */
const isFile = (path: string) => {
  const lastPart = path.split("/").pop() || "";
  // Must have a dot, and something before the dot (not just an extension like ".py")
  return lastPart.includes(".") && lastPart.lastIndexOf(".") > 0;
};

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
const getComponents = (getUniqueId: (text: string) => string, owner: string, repo: string): Components => ({
  em({ children }) {
    const text = String(children);
    if (!text.startsWith("Source:")) return <em className="italic">{children}</em>;

    // Content after "Source:"
    const content = text.replace(/^Source:\s*/, "");
    // Split by comma for multiple files or discontinuous segments
    const parts = content.split(",").map(p => p.trim()).filter(Boolean);
    
    let lastPath = "";

    return (
      <div className="flex items-center gap-2.5 my-4 flex-wrap">
        <span className="text-[12px] font-bold text-slate-400 uppercase tracking-widest shrink-0">Sources:</span>
        {parts.map((part, idx) => {
          // Detect if this part is just a line range for the previous file
          const isJustLines = /^(?:#?L?)?\d+(?:-L?\d+)?$/.test(part);
          
          let path = "";
          let lines = "";
          
          if (isJustLines && lastPath) {
            path = lastPath;
            lines = part;
          } else {
            let cleanPart = part;
            const blobMatch = part.match(/\/blob\/[^/]+\/(.+)$/);
            if (blobMatch) {
              cleanPart = blobMatch[1];
            }
            
            // Robust split: look for common separators followed by line number pattern
            // Pattern matches: [path][:][L][digits] or [path][#][L][digits]
            const splitMatch = cleanPart.match(/^(.*?)(?:[:#]+L?|#L)(\d.*)$/);
            
            if (splitMatch) {
              path = splitMatch[1].trim();
              lines = splitMatch[2].trim();
            } else {
              path = cleanPart;
              lines = "";
            }
            lastPath = path;
          }

          if (!isFile(path)) return null;
          
          return (
            <SourceBadge 
              key={`${path}-${idx}`} 
              path={path} 
              lines={lines} 
              owner={owner} 
              repo={repo} 
            />
          );
        })}
      </div>
    );
  },
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
  /** The title of the wiki page. (Optional since it is usually in the content) */
  title?: string;
  /** The Markdown content of the wiki page. */
  content: string;
  /** The repository owner. */
  owner: string;
  /** The repository name. */
  repo: string;
}

/**
 * Renders the content of a wiki page, including Markdown and Mermaid diagrams.
 * Automatically generates unique IDs for headings to support Table of Contents.
 */
export function WikiPageContent({ content, owner, repo }: Props) {
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

  const components = getComponents(getUniqueId, owner, repo);

  return (
    <article className="w-full max-w-4xl p-8 text-foreground mx-auto">
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
