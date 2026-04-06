import { Star, Clock, Code2, Globe } from "lucide-react";
import Link from "next/link";

/**
 * Props for the RepoCard component.
 */
interface RepoCardProps {
  /** The owner of the repository. */
  owner: string;
  /** The name of the repository. */
  name: string;
  /** A brief description of the repository. */
  description?: string;
  /** The number of stars the repository has. */
  stars?: number;
  /** The primary programming language of the repository. */
  language?: string;
  /** Human-readable string representing when the repository was last indexed. */
  updatedAt?: string;
  /** Language code the wiki was generated in (e.g. "en", "zh"). */
  wikiLanguage?: string;
}

/**
 * A card component that displays summary information about a repository.
 * Used on the home page for discovery.
 */
export function RepoCard({ owner, name, description, stars, language, updatedAt, wikiLanguage }: RepoCardProps) {
  return (
    <Link href={`/${owner}/${name}`} className="group block p-5 bg-card border border-border rounded-xl hover:border-primary/50 hover:shadow-sm transition-all">
      <h3 className="text-lg font-bold group-hover:text-primary transition-colors">
        <span className="text-muted-foreground font-normal">{owner}/</span>{name}
      </h3>
      <p className="mt-2 text-sm text-muted-foreground line-clamp-2 h-10">
        {description || "No description available."}
      </p>
      <div className="mt-4 flex items-center gap-4 text-xs text-muted-foreground">
        {language && (
          <span className="flex items-center gap-1.5">
            <Code2 size={14} className="text-primary" /> {language}
          </span>
        )}
        {stars !== undefined && (
          <span className="flex items-center gap-1.5">
            <Star size={14} className="text-yellow-500 fill-yellow-500" /> {stars.toLocaleString()}
          </span>
        )}
        {wikiLanguage && wikiLanguage !== "en" && (
          <span className="flex items-center gap-1.5">
            <Globe size={14} className="text-primary" />
            {wikiLanguage === "zh" ? "中文" : wikiLanguage.toUpperCase()}
          </span>
        )}
        <span className="flex items-center gap-1.5 ml-auto">
          <Clock size={14} /> {updatedAt || "Never indexed"}
        </span>
      </div>
    </Link>
  );
}
