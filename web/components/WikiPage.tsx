import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github-dark.css";

interface Props { title: string; content: string }

export function WikiPageContent({ title, content }: Props) {
  return (
    <article className="max-w-4xl p-8 text-foreground">
      <h1 className="text-3xl font-bold mb-6">{title}</h1>
      <div className="wiki-content">
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>{content}</ReactMarkdown>
      </div>
    </article>
  );
}
